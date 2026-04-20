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
    """Starts a Batch STT job for the provided audio file."""
    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        return jsonify({"success": False, "error": "SARVAM_API_KEY missing from environment."}), 500

    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    audio_file = request.files['file']
    if audio_file.filename == '':
        return jsonify({"success": False, "error": "Empty filename."}), 400

    try:
        filename = audio_file.filename
        file_bytes = audio_file.read()
        size_mb = len(file_bytes) / (1024 * 1024)

        # Get params from form
        model = request.form.get("model", "saaras:v3")
        mode = request.form.get("mode", "transcribe")
        with_diarization = request.form.get("with_diarization", "false").lower() == "true"
        language_code = request.form.get("language_code", "unknown")

        headers = {"api-subscription-key": api_key}

        # --- 1. Initiate Batch Job ---
        job_payload = {
            "inputs": [{"file_name": filename}],
            "job_parameters": {
                "model": model,
                "mode": mode,
                "with_diarization": with_diarization,
                "language_code": language_code
            }
        }
        
        print(f"Initializing Batch Job for {filename} ({size_mb:.2f}MB, Mode: {mode})...")
        init_res = requests.post("https://api.sarvam.ai/speech-to-text/job/v1", headers=headers, json=job_payload)
        if not init_res.ok:
            return jsonify({"success": False, "error": f"Init Error: {init_res.text}"}), init_res.status_code
        
        job_id = init_res.json().get("job_id")

        # --- 2. Get Upload URL ---
        url_payload = {"job_id": job_id, "files": [filename]}
        url_res = requests.post("https://api.sarvam.ai/speech-to-text/job/v1/upload-files", headers=headers, json=url_payload)
        if not url_res.ok:
            return jsonify({"success": False, "error": f"Upload URL Error: {url_res.text}"}), url_res.status_code
        
        presigned_url = None
        for item in url_res.json():
            if item.get("file_name") == filename:
                presigned_url = item.get("url")
        
        if not presigned_url:
            return jsonify({"success": False, "error": "Pre-signed URL retrieval failed."}), 500

        # --- 3. Upload File ---
        requests.put(presigned_url, data=file_bytes)
        
        # --- 4. Start Job ---
        start_res = requests.post(f"https://api.sarvam.ai/speech-to-text/job/v1/{job_id}/start", headers=headers)
        if not start_res.ok:
            return jsonify({"success": False, "error": f"Start Error: {start_res.text}"}), start_res.status_code

        # Return Job ID to frontend
        return jsonify({
            "success": True, 
            "batch_job_id": job_id, 
            "filename": filename, 
            "size_mb": f"{size_mb:.2f}",
            "mode": mode,
            "language_code": language_code
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/job-status/<job_id>', methods=['GET'])
def get_job_status(job_id):
    """Polls the Sarvam Batch STT job status and returns formatted transcript."""
    api_key = os.getenv("SARVAM_API_KEY")
    headers = {"api-subscription-key": api_key}
    
    try:
        status_res = requests.get(f"https://api.sarvam.ai/speech-to-text/job/v1/{job_id}/status", headers=headers)
        if not status_res.ok:
            return jsonify({"success": False, "error": f"Status Poll Error: {status_res.text}"}), status_res.status_code
        
        status_data = status_res.json()
        job_state = status_data.get("job_state")
        
        if job_state == "Completed":
            # Fetch and process results
            results_res = requests.post(f"https://api.sarvam.ai/speech-to-text/job/v1/{job_id}/results", headers=headers)
            if not results_res.ok:
                return jsonify({"success": False, "error": f"Results Error: {results_res.text}"}), results_res.status_code
            
            results_data = results_res.json()
            scripts = results_data.get("scripts", [])
            transcript_text = "Transcription failed or empty."
            
            if scripts:
                script = scripts[0]
                if "diarized_transcript" in script:
                    entries = script["diarized_transcript"].get("entries", [])
                    transcript_text = "\n\n".join([f"[Speaker {e.get('speaker_id')}]: {e.get('transcript')}" for e in entries])
                else:
                    transcript_text = script.get("transcript", "No transcript found.")

            # Save to persistent history
            filename = request.args.get("filename", "batch_file")
            size_mb = float(request.args.get("size_mb", 0))
            lang = request.args.get("lang", "unknown")
            mode = request.args.get("mode", "transcribe")
            save_to_db(filename, size_mb, lang, mode, transcript_text)
            
            return jsonify({"success": True, "state": "Completed", "transcript": transcript_text})
        
        elif job_state == "Failed":
            return jsonify({"success": True, "state": "Failed", "error": status_data.get("error_message", "Job failed.")})
        
        return jsonify({"success": True, "state": job_state})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def save_to_db(filename, size_mb, language_code, mode, transcript_text):
    """Persists records in SQLite history for 7 days."""
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
        print(f"DB Error: {e}")


if __name__ == '__main__':
    # Force run on port 5001 or fallback
    app.run(debug=True, port=5001, host='0.0.0.0')
