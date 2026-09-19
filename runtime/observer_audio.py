"""Short voice recordings -> OpenRouter transcript. Does not interpret or execute orders."""
import base64
import io
import wave
from primitives.vision import api_json, credentials

MAX_AUDIO_BYTES = 8 * 1024 * 1024


def transcribe(data, format):
    if format not in ('wav', 'webm', 'ogg', 'm4a'):
        raise ValueError('Voice input must be WAV, WebM, Ogg or M4A.')
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise ValueError('Record a short voice request (at most 8 MB).')
    signatures = {
        'wav': data.startswith(b'RIFF') and data[8:12] == b'WAVE',
        'webm': data.startswith(b'\x1aE\xdf\xa3'),
        'ogg': data.startswith(b'OggS'),
        'm4a': data[4:8] == b'ftyp',
    }
    if not signatures[format]:
        raise ValueError('The recording does not match its audio format.')
    if format == 'wav':
        try:
            with wave.open(io.BytesIO(data)) as recording:
                duration = recording.getnframes() / recording.getframerate()
                if not 0.1 <= duration <= 30:
                    raise ValueError('Record between 0.1 and 30 seconds of speech.')
        except (wave.Error, EOFError) as exc:
            raise ValueError('The WAV recording is invalid.') from exc
    model = credentials()['OPENROUTER_STT_MODEL']
    result = api_json('audio/transcriptions', {
        'model': model, 'input_audio': {'data': base64.b64encode(data).decode(), 'format': format},
        'language': 'en', 'temperature': 0}, timeout=60)
    text = result.get('text')
    if not isinstance(text, str) or len(text) > 2000:
        raise ValueError('The transcription response was invalid.')
    if not text.strip():
        raise ValueError('No speech was recognized. Try again or type your request.')
    return {'text': text.strip(), 'model': model}
