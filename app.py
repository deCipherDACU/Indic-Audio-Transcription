import os
import sqlite3
import json
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import requests

load_dotenv()

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # Allow 500MB max payload

DB_PATH = os.path.join(os.path.dirname(__file__), 'history.db')

def init_db():
    """Initialize the SQLite database and create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS transcriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            size_mb REAL,
            language_code TEXT,
            mode TEXT,
            transcript TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def cleanup_old_history():
    """Deletes records older than 7 days to keep the database lightweight."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    c.execute('DELETE FROM transcriptions WHERE created_at < ?', (seven_days_ago,))
    conn.commit()
    conn.close()

# Initialize DB on startup
init_db()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/history', methods=['GET'])
def get_history():
    """Returns the transcription history (up to 7 days)."""
    cleanup_old_history() # Ensure old data is purged
    
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('SELECT * FROM transcriptions ORDER BY created_at DESC')
        rows = c.fetchall()
        
        history = []
        for row in rows:
            history.append({
                'id': row['id'],
                'filename': row['filename'],
                'size_mb': f"{row['size_mb']:.2f}",
                'language_code': row['language_code'],
                'mode': row['mode'],
                'transcript': row['transcript'],
                'created_at': row['created_at']
            })
        conn.close()
        
        return jsonify({'success': True, 'data': history})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/history', methods=['DELETE'])
def clear_history():
    """Wipes the entire history database manually."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM transcriptions')
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    # Only load API key from secure environment variable
    api_key = os.getenv("SARVAM_API_KEY")
        
    if not api_key:
        return jsonify({"success": False, "error": "SARVAM_API_KEY not found in server environment configuration - backend deployment error."}), 500

    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No audio file provided in the request payload."}), 400

    audio_file = request.files['file']
    if audio_file.filename == '':
        return jsonify({"success": False, "error": "No file selected."}), 400

        # ----------------------------------------------------
        # BRANCH: Diarization / Batch Mode
        # ----------------------------------------------------
        if with_diarization:
            print(f"Diarization enabled. Switching to Batch STT Workflow for {filename}...")
            
            # 1. Initiate Job
            job_init_payload = {
                "inputs": [{"file_name": filename}],
                "job_parameters": {
                    "model": model,
                    "mode": mode,
                    "with_diarization": True
                }
            }
            init_res = requests.post(
                "https://api.sarvam.ai/speech-to-text/job/v1",
                headers=headers,
                json=job_init_payload
            )
            if not init_res.ok:
                return jsonify({"success": False, "error": f"Batch Init Error: {init_res.text}"}), init_res.status_code
            
            job_id = init_res.json().get("job_id")
            
            # 2. Get Upload URLs
            upload_url_payload = {
                "job_id": job_id,
                "files": [filename]
            }
            url_res = requests.post(
                "https://api.sarvam.ai/speech-to-text/job/v1/upload-files",
                headers=headers,
                json=upload_url_payload
            )
            if not url_res.ok:
                return jsonify({"success": False, "error": f"Upload URL Error: {url_res.text}"}), url_res.status_code
            
            upload_data = url_res.json()
            # Expecting a list of {file_name, url}
            presigned_url = None
            for item in upload_data:
                if item.get("file_name") == filename:
                    presigned_url = item.get("url")
            
            if not presigned_url:
                return jsonify({"success": False, "error": "Could not obtain pre-signed upload URL from Sarvam."}), 500

            # 3. Upload File (PUT)
            # Use audio_file.mimetype if possible, or fallback
            upload_res = requests.put(presigned_url, data=file_bytes)
            if not upload_res.ok:
                return jsonify({"success": False, "error": f"File Storage Upload Error: {upload_res.text}"}), 500
            
            # 4. Start Job
            start_res = requests.post(
                f"https://api.sarvam.ai/speech-to-text/job/v1/{job_id}/start",
                headers=headers
            )
            if not start_res.ok:
                return jsonify({"success": False, "error": f"Batch Start Error: {start_res.text}"}), start_res.status_code

            # Return job_id to frontend for polling
            return jsonify({
                "success": True,
                "batch_job_id": job_id,
                "filename": filename,
                "size_mb": f"{size_mb:.2f}",
                "mode": mode,
                "language_code": language_code
            })

        # ----------------------------------------------------
        # BRANCH: Real-time STT (Synchronous)
        # ----------------------------------------------------
        data = {
            'language_code': language_code,
            'model': model,
            'mode': mode,
            'with_diarization': "false" # Real-time doesn't support it
        }
        
        sarvam_response = requests.post(
            "https://api.sarvam.ai/speech-to-text",
            headers=headers,
            files=files,
            data=data
        )

        # Bubble up any direct api errors smoothly
        if not sarvam_response.ok:
            error_message = f"API Error {sarvam_response.status_code}: {sarvam_response.text}"
            return jsonify({"success": False, "error": error_message}), sarvam_response.status_code

        response_json = sarvam_response.json()
        
        # Identify the exact transcription text
        transcript_text = ""
        if 'transcript' in response_json:
            transcript_text = response_json['transcript']
        elif 'data' in response_json and 'transcript' in response_json['data']:
            transcript_text = response_json['data']['transcript']
        else:
            transcript_text = json.dumps(response_json, indent=2)

        # ----------------------------------------------------
        # Save to Database History (7-Day Cache)
        # ----------------------------------------------------
        save_to_db(filename, size_mb, language_code, mode, transcript_text)

        return jsonify({
            "success": True,
            "data": response_json,
            "transcript_preview": transcript_text
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


def save_to_db(filename, size_mb, language_code, mode, transcript_text):
    """Helper to persist transcription results."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO transcriptions (filename, size_mb, language_code, mode, transcript)
            VALUES (?, ?, ?, ?, ?)
        ''', (filename, size_mb, language_code, mode, transcript_text))
        conn.commit()
        conn.close()
        cleanup_old_history()
    except Exception as e:
        print(f"Database save error: {e}")


@app.route('/api/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """Polls the Sarvam Batch STT job status and retrieves results if completed."""
    api_key = os.getenv("SARVAM_API_KEY")
    headers = {"api-subscription-key": api_key}
    
    try:
        # 1. Check Status
        status_res = requests.get(
            f"https://api.sarvam.ai/speech-to-text/job/v1/{job_id}/status",
            headers=headers
        )
        if not status_res.ok:
            return jsonify({"success": False, "error": f"Status Poll Error: {status_res.text}"}), status_res.status_code
        
        status_data = status_res.json()
        job_state = status_data.get("job_state")
        
        if job_state == "Completed":
            # 2. Fetch Results
            results_res = requests.post(
                f"https://api.sarvam.ai/speech-to-text/job/v1/{job_id}/results",
                headers=headers
            )
            if not results_res.ok:
                return jsonify({"success": False, "error": f"Results Fetch Error: {results_res.text}"}), results_res.status_code
            
            results_data = results_res.json()
            
            # Extract transcript from the first file (since we only upload one)
            # Structure: {"job_id": "...", "scripts": [{"file_name": "...", "transcript": "...", "diarized_transcript": {...}}]}
            scripts = results_data.get("scripts", [])
            transcript_text = "No transcript found."
            if scripts:
                script = scripts[0]
                if "diarized_transcript" in script:
                    # Format diarized output nicely if available
                    entries = script["diarized_transcript"].get("entries", [])
                    if entries:
                        formatted_parts = []
                        for entry in entries:
                            speaker = f"Speaker {entry.get('speaker_id')}"
                            formatted_parts.append(f"[{speaker}]: {entry.get('transcript')}")
                        transcript_text = "\n\n".join(formatted_parts)
                    else:
                        transcript_text = script.get("transcript", "")
                else:
                    transcript_text = script.get("transcript", "")

            # 3. Save to History
            # We need to get original metadata from the request context or just assume it?
            # Actually, the frontend should send metadata if it wants it saved accurately.
            # But for now, we'll save what we have.
            filename = request.args.get("filename", "unknown_batch_file")
            size_mb = float(request.args.get("size_mb", 0))
            mode = request.args.get("mode", "transcribe")
            lang = request.args.get("lang", "unknown")
            
            save_to_db(filename, size_mb, lang, mode, transcript_text)
            
            return jsonify({
                "success": True,
                "state": "Completed",
                "transcript": transcript_text,
                "full_data": results_data
            })
        
        elif job_state == "Failed":
            error_msg = status_data.get("error_message", "Unknown batch error.")
            return jsonify({"success": True, "state": "Failed", "error": error_msg})
        
        else:
            # Still Pending, Running, etc.
            return jsonify({"success": True, "state": job_state})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    # Force run on port 5001 or fallback
    app.run(debug=True, port=5001, host='0.0.0.0')
