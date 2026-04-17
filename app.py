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

    try:
        filename = audio_file.filename
        file_bytes = audio_file.read()
        
        # Calculate size before reading further
        size_mb = len(file_bytes) / (1024 * 1024)

        # Sarvam params
        language_code = request.form.get("language_code", "unknown")
        model = request.form.get("model", "saaras:v3")
        mode = request.form.get("mode", "transcribe")
        with_diarization = request.form.get("with_diarization", "false").lower() == "true"
        
        headers = {
            "api-subscription-key": api_key,
        }
        
        files = {
            'file': (filename, file_bytes, audio_file.mimetype or 'audio/mpeg')
        }
        
        data = {
            'language_code': language_code,
            'model': model,
            'mode': mode,
            'with_diarization': str(with_diarization).lower()
        }
        
        print(f"Bypassing Vercel limits... Sending {size_mb:.2f}MB file directly from Python Backend to Sarvam AI...")

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
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            INSERT INTO transcriptions (filename, size_mb, language_code, mode, transcript)
            VALUES (?, ?, ?, ?, ?)
        ''', (filename, size_mb, language_code, mode, transcript_text))
        conn.commit()
        conn.close()
        
        # Clean up database asynchronously or inline
        cleanup_old_history()

        return jsonify({
            "success": True,
            "data": response_json,
            "transcript_preview": transcript_text
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    # Force run on port 5001 or fallback
    app.run(debug=True, port=5001, host='0.0.0.0')
