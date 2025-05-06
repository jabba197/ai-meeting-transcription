import os
import time
import logging
import mimetypes
import google.generativeai as genai
from dotenv import load_dotenv
from typing import Union, Tuple, Optional

# Setup logger for this module
logger = logging.getLogger(__name__)

# Load environment variables (especially GEMINI_API_KEY)
load_dotenv()

# --- Gemini Configuration --- 
_gemini_configured = False
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    logger.warning("GEMINI_API_KEY environment variable not set. Transcription will be disabled.")
else:
    try:
        genai.configure(api_key=api_key)
        _gemini_configured = True # Set flag on success
        logger.info("Gemini API configured successfully.")
    except Exception as e:
        logger.error(f"Failed to configure Gemini API: {e}", exc_info=True)
        _gemini_configured = False # Ensure flag is false on error

# --- Model Names --- #
MODEL_NAME = 'gemini-2.0-flash'  # For transcription
RAG_KEYWORD_MODEL_NAME = 'gemini-2.0-flash' # For RAG keyword generation
MULTIMODAL_SUMMARY_MODEL_NAME = 'gemini-2.5-pro-preview-05-06' # For multimodal summarization

# --- Constants --- 
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# --- Supported Audio Formats and MIME types ---
# Supported audio formats based on https://ai.google.dev/gemini-api/docs/prompting_with_media#supported_file_formats
# This list can be expanded as needed.
SUPPORTED_AUDIO_FORMATS = {
    ".aac": "audio/aac",
    ".aiff": "audio/aiff",
    ".flac": "audio/flac",
    ".m4a": "audio/m4a",
    ".mp3": "audio/mpeg", # Common MIME type for .mp3
    ".oga": "audio/ogg",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".wav": "audio/wav",
}

def get_mime_type(file_path: str) -> Optional[str]:
    """Determine the MIME type of a file based on its extension or by guessing."""
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()

    # Check predefined supported formats first
    if ext in SUPPORTED_AUDIO_FORMATS:
        return SUPPORTED_AUDIO_FORMATS[ext]

    # Fallback to mimetypes library if not in our predefined list
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type and mime_type.startswith("audio/"):
        logging.info(f"Guessed MIME type for {file_path} ({ext}): {mime_type}")
        return mime_type
    
    logging.warning(f"Could not determine a specific audio MIME type for {file_path} with extension {ext}. Fallback guess: {mime_type}")
    # If mimetypes also fails to provide an audio/* type, or if it's a generic type like application/octet-stream
    # it's better to return None and let the calling function decide or raise an error.
    # However, for Gemini, even application/octet-stream might work if the underlying format is supported.
    # For now, let's be explicit if we can't map it to an audio/* type we know.
    return None # Or potentially mime_type if we want to try generic ones

def transcribe_audio(audio_file_path: str) -> Tuple[Union[str, None], float, str]:
    """
    Transcribes the given audio file using the Gemini API.

    Args:
        audio_file_path: The path to the audio file.

    Returns:
        A tuple containing:
            - The transcript text (str) or None if transcription fails.
            - Time taken for transcription in seconds (float).
            - The model name used for transcription (str).
    """
    # Check if Gemini was configured successfully
    if not _gemini_configured:
        logger.error("Transcription failed: Gemini API is not configured (check API key and logs).")
        return None, 0.0, MODEL_NAME

    logger.info(f"Attempting to transcribe audio file: {audio_file_path}")
    if not os.path.exists(audio_file_path):
        logger.error(f"Audio file not found at {audio_file_path}")
        return None, 0.0, MODEL_NAME

    final_mime_type = get_mime_type(audio_file_path)

    if not final_mime_type:
        logger.error(f"Unsupported audio file format or unable to determine MIME type for {audio_file_path}. Please use one of the supported formats: {', '.join(SUPPORTED_AUDIO_FORMATS.keys())}")
        return None, 0.0, MODEL_NAME

    logger.info(f"Uploading {audio_file_path} with MIME type: {final_mime_type}")

    uploaded_file = None # Initialize to None
    transcription_start_time = time.time()

    try:
        logger.info(f"Uploading audio file: {audio_file_path}...")
        uploaded_file = genai.upload_file(path=audio_file_path, mime_type=final_mime_type)
        logger.info(f"Successfully uploaded audio file: {uploaded_file.name} - {uploaded_file.uri}")
        logger.debug(f"Initial file state: {uploaded_file.state.name}") # Use debug level

        # Wait for the file to be processed
        while uploaded_file.state.name == "PROCESSING":
            logger.debug('.', end='', flush=True) # Use debug level
            time.sleep(5) 
            uploaded_file = genai.get_file(uploaded_file.name)
            logger.debug(f"File state: {uploaded_file.state.name}") # Use debug level

        if uploaded_file.state.name == "FAILED":
            logger.error(f"Audio file processing failed: {uploaded_file.state}")
            # Attempt to delete the failed file
            try:
                logger.warning(f"Deleting failed file: {uploaded_file.name}")
                genai.delete_file(uploaded_file.name)
            except Exception as delete_error:
                logger.error(f"Error deleting failed file: {delete_error}")
            return None, round(time.time() - transcription_start_time, 2), MODEL_NAME

        logger.info(f"Audio file ready: {uploaded_file.name}")

        # Create the generative model instance
        model = genai.GenerativeModel(model_name=MODEL_NAME)

        # Make the transcription request
        logger.info("Sending transcription request to Gemini...")
        response = model.generate_content(
            [
                "Please transcribe this audio accurately. group transcriptions into sections by speaker.", # Simple prompt
                uploaded_file
            ],
            request_options={"timeout": 600} # 10 minutes timeout
        )

        # Clean up the uploaded file *after* getting the response
        logger.info(f"Transcription response received. Deleting uploaded file: {uploaded_file.name}")
        genai.delete_file(uploaded_file.name)
        uploaded_file = None  # Reset after deletion

        # Handle transcription response via candidates and content parts
        if response.candidates and response.candidates[0].content.parts:
            parts = response.candidates[0].content.parts
            transcript = ''.join(part.text for part in parts)
            # Remove double quotes from the transcript before returning
            final_transcript = transcript.replace('"', '')
            logger.info("Transcription successful.")
            return final_transcript, round(time.time() - transcription_start_time, 2), MODEL_NAME
        else:
            logger.error("No transcription content found in response candidates.")
            logger.debug(f"Full transcription response: {response}")
            return None, round(time.time() - transcription_start_time, 2), MODEL_NAME

    except Exception as e:
        logger.error(f"An error occurred during transcription: {e}", exc_info=True)
        # Attempt to clean up if file was uploaded and not yet deleted
        if uploaded_file and hasattr(uploaded_file, 'name'):
            try:
                logger.warning(f"Attempting to delete file due to error: {uploaded_file.name}")
                genai.delete_file(uploaded_file.name)
            except Exception as delete_error:
                logger.error(f"Error deleting file during error cleanup: {delete_error}")
        return None, round(time.time() - transcription_start_time, 2), MODEL_NAME

if __name__ == '__main__':
    # Example usage setup
    # logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s') # Moved to create_app
    
    test_audio_path = "test_audio.mp3" # <--- CHANGE THIS

    if not _gemini_configured:
        logger.error("\nPlease set the GEMINI_API_KEY environment variable in a .env file and ensure it's valid.")
    elif os.path.exists(test_audio_path):
        logger.info(f"\nRunning test transcription for: {test_audio_path}")
        start_time = time.time()
        transcript_result, time_taken, model_name = transcribe_audio(test_audio_path)
        end_time = time.time()
        logger.info(f"Transcription process took {end_time - start_time:.2f} seconds.")

        if transcript_result:
            logger.info("\n--- Transcript ---")
            logger.info(f"Transcript result: {transcript_result}")
            logger.info(f"Time taken: {time_taken} seconds")
            logger.info(f"Model name: {model_name}")
            logger.info("--- End Transcript ---")
        else:
            logger.info("\nTranscription failed (check logs for details).")
    else:
        logger.warning(f"\nTest audio file not found: {test_audio_path}")
        logger.warning("Please update the 'test_audio_path' variable in transcription.py")
        logger.warning("and ensure the audio file exists to run a test.")
