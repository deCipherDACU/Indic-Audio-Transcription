# Indic-Audio-Transcription

A hyper-minimalist, production-ready full-stack application that leverages the Sarvam AI API for ultra-accurate speech-to-text processing for Indian languages. Built with a Python/Flask backend and a deeply stylized ElevenLabs-inspired aesthetic frontend. 

## Features
- **ElevenLabs Tech Aesthetic:** A sleek black, white, and zinc interface with pure HTML/Tailwind CSS styling.
- **500MB Upload Bypasses:** Powered by an active Python (Gunicorn/Flask) backend backend designed to securely handle massive audio uploads beyond typical Vercel constraints.
- **7-Day SQLite Memory:** Integrated robust `sqlite3` historical data logging. Retrieves history seamlessly, while effortlessly purging records older than 7 days.
- **Language Auto-Detection:** Bypasses manual dropdowns to force AI inference to immediately locate and translate localized vernaculars dynamically.

## Installation
1. Clone this repository.
2. Ensure you have Python installed.
3. Establish your environment: `pip install -r requirements.txt`
4. Set up the `.env` file explicitly with:
```
SARVAM_API_KEY=sk_your_key_here
```
5. Deploy: `python app.py` and navigate to local port `5001`.

## Deployment
Use **Render.com** (Web Service). Establish the GitHub connection. Set your start command to: `gunicorn app:app`. Configure your environment API key inside Render parameters. Keep an eye on storage handling regarding `history.db`.
