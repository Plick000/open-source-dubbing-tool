import os
import time
import json
import mimetypes
import requests
import re
import subprocess
import pycountry
import shutil



from difflib import SequenceMatcher
from pathlib import Path
from dotenv import load_dotenv
from faster_whisper import WhisperModel

# ---------------------------------------
# CONFIG
# ---------------------------------------
BASE_URL = "https://api.elevenlabs.io"
POLL_INTERVAL_SEC = 5       # how often to check status
MAX_WAIT_SEC = 15 * 60      # max wait 15 min

# Validation config
SIMILARITY_THRESHOLD = 0.90   # strict 90%
MAX_RETRIES = 10               # how many times to re-dub if below 90%


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "inputs" / "config" / "config.json"


t0 = time.time()

class ElevenDubFailed(RuntimeError):
    def __init__(self, dubbing_id: str, meta: dict):
        super().__init__(f"ElevenLabs dubbing failed: {dubbing_id}")
        self.dubbing_id = dubbing_id
        self.meta = meta


# ---------------------------------------
# ENV + API
# ---------------------------------------

load_dotenv(override=True)

_RAW_ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVEN_API_KEY = _RAW_ELEVEN_API_KEY.replace("\r", "").replace("\n", "").strip()

# Faster-Whisper config (large-v3)
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")

def resolve_whisper_model_dir(shared_dir: str) -> str:
    """
    If WHISPER_LOCAL_CACHE is set, copy the model from shared_dir -> local cache once,
    then load from local for fast startup. Uses a simple lock folder to prevent overlap.
    """
    shared = Path(shared_dir)
    local_raw = (os.getenv("WHISPER_LOCAL_CACHE", "") or "").strip()
    if not local_raw:
        return str(shared)

    local = Path(local_raw)

    # Already cached?
    if (local / "model.bin").is_file():
        return str(local)

    # Simple lock to prevent multiple processes copying at once
    lock_dir = Path(str(local) + ".lock")

    while True:
        try:
            lock_dir.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            time.sleep(0.5)
            if (local / "model.bin").is_file():
                return str(local)

    try:
        tmp = Path(str(local) + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)

        tmp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(shared, tmp, dirs_exist_ok=True)

        if local.exists():
            shutil.rmtree(local, ignore_errors=True)

        tmp.rename(local)
        return str(local)
    finally:
        try:
            lock_dir.rmdir()
        except Exception:
            pass


def default_whisper_model_dir():
    # Windows default
    win_path = r"Z:\Automated Dubbings\_models\whisper\faster-whisper-large-v3"
    # WSL/Linux default path for the same Z: drive
    wsl_path = "/mnt/z/Automated Dubbings/_models/whisper/faster-whisper-large-v3"

    # If running on Windows, use Windows path; otherwise use WSL/Linux path
    if os.name == "nt":
        return win_path
    return wsl_path

WHISPER_MODEL_DIR_RAW = os.getenv("WHISPER_MODEL_DIR", default_whisper_model_dir())

print(f"Whisper shared model path: {WHISPER_MODEL_DIR_RAW}")

if not os.path.isdir(WHISPER_MODEL_DIR_RAW):
    raise FileNotFoundError(
        f"Whisper model directory not found: {WHISPER_MODEL_DIR_RAW}\n"
        "Make sure the shared drive is mounted (Windows: Z: exists; WSL: /mnt/z exists) "
        "or set WHISPER_MODEL_DIR to the correct path."
    )

WHISPER_MODEL_DIR = resolve_whisper_model_dir(WHISPER_MODEL_DIR_RAW)

print(f"Loading Faster-Whisper model from (effective): {WHISPER_MODEL_DIR}")

whisper_model = WhisperModel(
    WHISPER_MODEL_DIR,
    device=WHISPER_DEVICE,
    compute_type=WHISPER_COMPUTE_TYPE,
)

print("Faster-Whisper model loaded.")


print("Faster-Whisper model loaded.")


def ensure_api_key():
    if not ELEVEN_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY not set in environment (.env)")


def eleven_headers():
    return {"xi-api-key": ELEVEN_API_KEY, "Accept": "application/json"}


def load_target_lang(config_path: str = CONFIG_PATH, default: str = "") -> str:
    if not os.path.isfile(config_path):
        print(f"⚠ Config not found: {config_path} → using default '{default}'")
        return default

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Try common keys (supports a few possible JSON structures)
    lang = (
        cfg.get("target_lang")
        or cfg.get("targetLanguage")
        or cfg.get("language")
        or (cfg.get("dubbing", {}) or {}).get("target_lang")
        or (cfg.get("dubbing", {}) or {}).get("targetLanguage")
        or default
    )

    lang = str(lang).strip().lower()
    return lang or default

def language_name_to_code(language_name: str) -> str:
    # "polish" -> "pl", "french" -> "fr", etc (no hardcoded list)
    lang = pycountry.languages.lookup(language_name.strip())
    code = getattr(lang, "alpha_2", None) or getattr(lang, "alpha_3", None)
    if not code:
        raise ValueError(f"Could not derive ISO code from language name: {language_name}")
    return code.lower()


def pick_speakers_and_mode(attempt: int):
    speaker_plan = [0, 2, 3, 4]
    idx = min(attempt - 1, len(speaker_plan) - 1)
    return speaker_plan[idx], "automatic"


# ---------------------------------------
# CORE ELEVENLABS CALLS
# ---------------------------------------
def create_dub_job(
    audio_path: str,
    project_name: str,
    source_lang: str = "auto",
    target_lang: str = "pl",
    num_speakers: int = 0,          # ✅ new
    mode: str = "automatic",        # ✅ new
) -> str:
    """
    Create a dubbing job on ElevenLabs.
    Returns: dubbing_id
    """
    ensure_api_key()

    url = f"{BASE_URL}/v1/dubbing"

    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    mime_type, _ = mimetypes.guess_type(audio_path)
    if mime_type is None and audio_path.lower().endswith(".mp3"):
        mime_type = "audio/mpeg"
    if mime_type is None:
        mime_type = "application/octet-stream"

    data = {
    "name": project_name or "Interview Dub",
    "source_lang": source_lang if source_lang and source_lang != "auto" else "auto",
    "target_lang": target_lang,

    "num_speakers": str(num_speakers),  # ✅ was "0"
    "mode": mode,                       # ✅ was "automatic"

    "watermark": "false",
    "highest_resolution": "false",
    }

    with open(audio_path, "rb") as f:
        files = {
            "file": (os.path.basename(audio_path), f, mime_type)
        }
        resp = requests.post(
            url,
            headers=eleven_headers(),
            data=data,
            files=files,
        )

    if resp.status_code != 200:
        print("=== ELEVENLABS DUB ERROR ===")
        print("Status:", resp.status_code)
        print("Body:", resp.text[:2000])
        print("============================")
        resp.raise_for_status()

    data = resp.json()
    dubbing_id = data["dubbing_id"]
    expected_duration = data.get("expected_duration_sec")
    if expected_duration is not None:
        print(f"Created dubbing job: {dubbing_id}, expected duration ~{expected_duration:.2f}s")
    else:
        print(f"Created dubbing job: {dubbing_id}")
    return dubbing_id


def get_dub_metadata(dubbing_id: str) -> dict:
    ensure_api_key()
    url = f"{BASE_URL}/v1/dubbing/{dubbing_id}"
    resp = requests.get(url, headers=eleven_headers())
    resp.raise_for_status()
    return resp.json()


def wait_for_dub_ready(
    dubbing_id: str,
    poll_interval: int = POLL_INTERVAL_SEC,
    max_wait: int = MAX_WAIT_SEC
) -> dict:
    """
    Polls ElevenLabs until dubbing status is 'dubbed'.
    If status becomes 'failed' -> raise ElevenDubFailed immediately.
    """
    print(f"Waiting for dubbing job {dubbing_id} to finish...")

    start = time.time()
    last_status = None

    while True:
        meta = get_dub_metadata(dubbing_id)
        status = (meta.get("status") or "unknown").lower()

        # print only when status changes (prevents spam)
        if status != last_status:
            print(f"  status = {status}")
            last_status = status

        if status == "dubbed":
            print("✔ Dubbing completed!")
            return meta

        if status == "failed":
            # raise with full metadata so you can see reason fields if present
            raise ElevenDubFailed(dubbing_id, meta)

        if time.time() - start > max_wait:
            raise TimeoutError(f"Dubbing job {dubbing_id} not ready after {max_wait} seconds")

        time.sleep(poll_interval)


def download_dubbed_audio(
    dubbing_id: str,
    language_code: str,
    output_path: str
):
    """
    Download dubbed audio for given language.
    """
    ensure_api_key()
    url = f"{BASE_URL}/v1/dubbing/{dubbing_id}/audio/{language_code}"
    resp = requests.get(url, headers=eleven_headers(), stream=True)
    resp.raise_for_status()

    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print(f"✔ Dubbed audio saved → {output_path}")


def force_stereo_ffmpeg(input_path: str):
    """
    Convert mono audio to true stereo (L = R).
    Overwrites the same file safely.
    """
    tmp_out = input_path + ".stereo.tmp.mp3"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-filter_complex", "pan=stereo|c0=c0|c1=c0",
        "-ac", "2",
        tmp_out,
    ]

    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True
    )

    os.replace(tmp_out, input_path)


def get_transcript_once(dubbing_id: str, language_code: str):
    """
    Try to fetch transcript once.
    Returns:
      - list of utterances if ready
      - None if 425 (not ready yet)
    """
    ensure_api_key()
    url = f"{BASE_URL}/v1/dubbing/{dubbing_id}/transcript/{language_code}"
    params = {"format_type": "json"}
    resp = requests.get(url, headers=eleven_headers(), params=params)

    if resp.status_code == 425:
        return None

    resp.raise_for_status()
    data = resp.json()
    return data.get("utterances", [])


def wait_for_transcript(
    dubbing_id: str,
    language_code: str,
    poll_interval: int = POLL_INTERVAL_SEC,
    max_wait: int = MAX_WAIT_SEC
):
    """
    Poll until transcript is available for language_code.
    Returns list of utterances.
    """
    print(f"Waiting for transcript of {language_code}...")

    start = time.time()
    while True:
        utts = get_transcript_once(dubbing_id, language_code)
        if utts is not None and len(utts) > 0:
            print(f"✔ Transcript ready for {language_code}, {len(utts)} segments")
            return utts

        if time.time() - start > max_wait:
            raise TimeoutError(f"Transcript for {language_code} not ready after {max_wait} seconds")

        print("  transcript not ready yet, polling again...")
        time.sleep(poll_interval)


# ---------------------------------------
# WHISPER + VALIDATION HELPERS
# ---------------------------------------
def utterances_to_text(utts) -> str:
    """
    Flatten ElevenLabs 'utterances' JSON into a single text string.
    """
    lines = []
    candidate_keys = ["text", "translation", "translated_text", "dubbed_text", "transcription"]
    for u in utts or []:
        if not isinstance(u, dict):
            continue
        text_val = ""
        for k in candidate_keys:
            if k in u and u[k]:
                text_val = str(u[k])
                break
        if text_val:
            lines.append(text_val.strip())
    return " ".join(lines).strip()


def normalize_text(text: str) -> str:
    """
    Normalize text for comparison.
    - lowercase
    - remove punctuation
    - collapse whitespace
    """
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def text_similarity(a: str, b: str) -> float:
    """
    Returns similarity between 0 and 1.
    """
    a_norm = normalize_text(a or "")
    b_norm = normalize_text(b or "")

    if not a_norm or not b_norm:
        return 0.0

    return SequenceMatcher(None, a_norm, b_norm).ratio()


def transcribe_with_whisper(audio_path: str, language_code: str = None) -> str:
    """
    Transcribe audio using Faster-Whisper large-v3.

    - If language_code is None or empty -> auto-detect language
    - If language_code is provided (e.g., "pl", "fr") -> force that language
    """
    lang = (language_code or "").strip().lower() or None
    print(f"▶ Whisper transcription on {audio_path} (lang={'auto' if lang is None else lang})...")

    segments, info = whisper_model.transcribe(
        audio_path,
        language=lang,          # None => auto-detect
        beam_size=5,
        best_of=5,
        temperature=0.0,
    )

    chunks = []
    for seg in segments:
        if seg.text:
            chunks.append(seg.text.strip())

    full_text = " ".join(chunks).strip()
    detected = getattr(info, "language", None) or getattr(info, "language_code", None) or "unknown"
    print(f"✔ Whisper transcription done, {len(full_text)} chars. Detected: {detected}")
    return full_text


# ---------------------------------------
# SINGLE-FILE PIPELINE (WITH VALIDATION)
# ---------------------------------------
def dub_interview(
    mp3_path: str,
    project_name: str,
    target_lang: str = "pl",
    source_lang: str = "auto",
    output_dir: str = "interview_dubs",
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    max_retries: int = MAX_RETRIES,
):
    """
    Full pipeline with validation + auto-regeneration:

      For up to max_retries:
        1) Create dubbing
        2) Wait until 'dubbed'
        3) Download dubbed audio
        4) Download original + dubbed transcripts
        5) Run Whisper (large-v3) on dubbed audio
        6) Compare Whisper transcript vs ElevenLabs dubbed transcript
        7) If similarity >= similarity_threshold, accept & save
           Else, regenerate and repeat.

      If still below threshold after max_retries → raise error.
    """
    os.makedirs(output_dir, exist_ok=True)

    last_similarity = 0.0
    last_eleven_text = ""
    last_whisper_text = ""
    last_dubbing_id = None

    for attempt in range(1, max_retries + 1):
        print("\n=======================================")
        print(f"   DUBBING ATTEMPT {attempt}/{max_retries}")
        print("=======================================\n")

        # 1. Create dubbing job
        num_speakers, mode = pick_speakers_and_mode(attempt)
        print(f"🎛 Using num_speakers={num_speakers} mode={mode}")
        
        # 1. Create dubbing job
        try:
            dubbing_id = create_dub_job(
                audio_path=mp3_path,
                project_name=project_name,
                source_lang=source_lang,
                target_lang=target_lang,
                num_speakers=num_speakers,
                mode=mode,
            )
            last_dubbing_id = dubbing_id
        except Exception as e:
            print(f"⚠ Error creating ElevenLabs dubbing job on attempt {attempt}/{max_retries}: {e}")
            time.sleep(10)
            continue

        # 2. Wait until job is finished
        try:
            _meta = wait_for_dub_ready(dubbing_id)
        except ElevenDubFailed as e:
            print("❌ ElevenLabs marked job as FAILED (refunded on their side). Creating a new job...")
            try:
                print(json.dumps(e.meta, ensure_ascii=False, indent=2)[:2000])
            except Exception:
                pass
            time.sleep(10)
            continue  # next attempt creates a new job
        except Exception as e:
            print(f"⚠ Error while waiting for dubbing job on attempt {attempt}/{max_retries}: {e}")
            time.sleep(10)
            continue

        try:
            # 3. Download dubbed audio for this attempt
            attempt_audio_out = os.path.join(
                output_dir, f"dubbed_{target_lang}_attempt{attempt}.mp3"
            )

            download_dubbed_audio(dubbing_id, target_lang, attempt_audio_out)
            force_stereo_ffmpeg(attempt_audio_out)

            # 4. Download transcripts
            orig_utts = wait_for_transcript(dubbing_id, "original")
            dub_utts = wait_for_transcript(dubbing_id, target_lang)

            # ElevenLabs dubbed transcript -> flat text
            eleven_text = utterances_to_text(dub_utts)

            # 5. Whisper transcription of the final dubbed audio
            whisper_text = transcribe_with_whisper(
                attempt_audio_out, language_code=target_lang
            )

            # 6. Compare similarity
            similarity = text_similarity(eleven_text, whisper_text)
        except Exception as e:
            print(f"⚠ Error during pipeline steps on attempt {attempt}/{max_retries}: {e}")
            time.sleep(10)
            continue


        # 6. Compare similarity
        last_similarity = similarity
        last_eleven_text = eleven_text
        last_whisper_text = whisper_text

        print(f"🔍 Similarity (Whisper vs Eleven transcript): {similarity:.4f}")

        if similarity >= similarity_threshold:
            # ACCEPT this dub
            final_audio_path = os.path.join(output_dir, f"dubbed_{target_lang}.mp3")
            os.replace(attempt_audio_out, final_audio_path)

            transcript_out = os.path.join(
                output_dir, f"transcript_{target_lang}.json"
            )
            with open(transcript_out, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "dubbing_id": dubbing_id,
                        "target_lang": target_lang,
                        "similarity": similarity,
                        "similarity_threshold": similarity_threshold,
                        "original": orig_utts,
                        "dubbed": dub_utts,
                        "eleven_text_flat": eleven_text,
                        "whisper_text": whisper_text,
                        "accepted_attempt": attempt,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            print(f"✔ Transcript + validation saved → {transcript_out}")
            print(
                f"🎉 Interview dubbing pipeline done (accepted on attempt {attempt})."
            )
            return

        # BELOW threshold – retry
        print(
            f"❌ Similarity {similarity:.4f} is below threshold {similarity_threshold:.2f}. "
            "Regenerating dub with the same source audio..."
        )

    # All retries failed
    debug_out = os.path.join(output_dir, f"failed_debug_{target_lang}.json")
    with open(debug_out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "last_dubbing_id": last_dubbing_id,
                "last_similarity": last_similarity,
                "similarity_threshold": similarity_threshold,
                "last_eleven_text_flat": last_eleven_text,
                "last_whisper_text": last_whisper_text,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("⚠ Failed to reach similarity threshold after all retries.")
    print(f"   Last similarity: {last_similarity:.4f}")
    print(f"   Debug info saved → {debug_out}")
    raise RuntimeError(
        "Dubbed audio could not be validated; similarity below threshold."
    )


# ---------------------------------------
# BATCH MODE HELPERS
# ---------------------------------------
def is_audio_file(filename: str) -> bool:
    audio_exts = {".mp3", ".wav", ".m4a", ".flac", ".ogg"}
    ext = os.path.splitext(filename)[1].lower()
    return ext in audio_exts


def dub_interviews_in_folder(
    input_dir: str,
    target_lang: str = "pl",
    source_lang: str = "auto",
    base_output_dir: str = "interview_dubs_batch",
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    max_retries: int = MAX_RETRIES,
):
    """
    Go through all audio files in input_dir, and dub them one by one.
    For each file:
      - create its own subfolder inside base_output_dir
      - run dub_interview() with validation + retries
    """
    if not os.path.isdir(input_dir):
        raise NotADirectoryError(f"Input directory not found: {input_dir}")

    os.makedirs(base_output_dir, exist_ok=True)

    files = sorted(os.listdir(input_dir))
    audio_files = [f for f in files if is_audio_file(f)]

    if not audio_files:
        print(f"No audio files found in: {input_dir}")
        return

    print(f"Found {len(audio_files)} audio file(s) in {input_dir}")
    for idx, fname in enumerate(audio_files, start=1):
        full_path = os.path.join(input_dir, fname)
        base_name, _ = os.path.splitext(fname)

        project_name = f"Interview Dub – {base_name} [{target_lang}]"
        file_output_dir = os.path.join(base_output_dir, base_name)

        print("\n====================================================")
        print(f" Processing file {idx}/{len(audio_files)}: {fname}")
        print(f" Output folder: {file_output_dir}")
        print("====================================================\n")

        os.makedirs(file_output_dir, exist_ok=True)

        try:
            dub_interview(
                mp3_path=full_path,
                project_name=project_name,
                target_lang=target_lang,
                source_lang=source_lang,
                output_dir=file_output_dir,
                similarity_threshold=similarity_threshold,
                max_retries=max_retries,
            )
        except Exception as e:
            print(f"⚠ Error processing {fname}: {e}")
            continue

    print("\n🎉 Batch dubbing done for all files in folder.")
    print(f"Total Time taken: {round(time.time() - t0, 2)} seconds.")

# ---------------------------------------
# CLI USAGE
# ---------------------------------------
if __name__ == "__main__":
    # MODE 1: Single file (if you want to test individually)
    # INPUT_MP3 = "interview_001_id1.mp3"
    # PROJECT_NAME = "Interview Test ES"
    # TARGET_LANG = "es"
    #
    # dub_interview(
    #     mp3_path=INPUT_MP3,
    #     project_name=PROJECT_NAME,
    #     target_lang=TARGET_LANG,
    #     source_lang="auto",
    #     output_dir="interview_dubs_ES_single",
    #     similarity_threshold=0.90,
    #     max_retries=3,
    # )

    # MODE 2: Batch – dub ALL audio files in a folder
    INPUT_DIR = PROJECT_ROOT / "output" / "samples" / "interviews"
    LANG_NAME = load_target_lang(config_path=CONFIG_PATH, default="")          # put all your audio files here
    if not LANG_NAME.strip():
        raise RuntimeError(f"No language found in {CONFIG_PATH}. Set 'language' to a full name like 'Polish'.")
    TARGET_LANG = language_name_to_code(LANG_NAME)
    SOURCE_LANG = "auto"
    LANG_DIRNAME = " ".join(LANG_NAME.strip().replace("-", " ").split()).title()
    BASE_OUTPUT_DIR = PROJECT_ROOT / "output" / "samples" / LANG_DIRNAME


    dub_interviews_in_folder(
        input_dir=INPUT_DIR,
        target_lang=TARGET_LANG,
        source_lang=SOURCE_LANG,
        base_output_dir=BASE_OUTPUT_DIR,
        similarity_threshold=0.75,  
        max_retries=10,
    )
