import os
import time
import logging
import google.generativeai as genai
from dotenv import load_dotenv
from typing import Union

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

# Model selection (using latest flash model)
MODEL_NAME = "models/gemini-2.5-flash-preview-04-17"

def transcribe_audio(audio_file_path: str) -> Union[str, None]:
    """
    Transcribes the given audio file using the Gemini API.

    Args:
        audio_file_path: The path to the audio file.

    Returns:
        The transcript text, or None if transcription fails.
    """
    # Check if Gemini was configured successfully
    if not _gemini_configured:
        logger.error("Transcription failed: Gemini API is not configured (check API key and logs).")
        return None

    logger.info(f"Attempting to transcribe audio file: {audio_file_path}")
    if not os.path.exists(audio_file_path):
        logger.error(f"Audio file not found at {audio_file_path}")
        return None

    uploaded_file = None # Initialize to None
    try:
        logger.info(f"Uploading audio file: {audio_file_path}...")
        uploaded_file = genai.upload_file(path=audio_file_path)
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
            return None

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
            return final_transcript
        else:
            logger.error("No transcription content found in response candidates.")
            logger.debug(f"Full transcription response: {response}")
            return None

    except Exception as e:
        logger.error(f"An error occurred during transcription: {e}", exc_info=True)
        # Attempt to clean up if file was uploaded and not yet deleted
        if uploaded_file and hasattr(uploaded_file, 'name'):
            try:
                logger.warning(f"Attempting to delete file due to error: {uploaded_file.name}")
                genai.delete_file(uploaded_file.name)
            except Exception as delete_error:
                logger.error(f"Error deleting file during error cleanup: {delete_error}")
        return None

if __name__ == '__main__':
    # Example usage setup
    # logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s') # Moved to create_app
    
    test_audio_path = "test_audio.mp3" # <--- CHANGE THIS

    if not _gemini_configured:
        logger.error("\nPlease set the GEMINI_API_KEY environment variable in a .env file and ensure it's valid.")
    elif os.path.exists(test_audio_path):
        logger.info(f"\nRunning test transcription for: {test_audio_path}")
        start_time = time.time()
        transcript_result = transcribe_audio(test_audio_path)
        end_time = time.time()
        logger.info(f"Transcription process took {end_time - start_time:.2f} seconds.")

        if transcript_result:
            logger.info("\n--- Transcript ---")
            logger.info(f"Transcript result: {transcript_result}")
            logger.info("--- End Transcript ---")
        else:
            logger.info("\nTranscription failed (check logs for details).")
    else:
        logger.warning(f"\nTest audio file not found: {test_audio_path}")
        logger.warning("Please update the 'test_audio_path' variable in transcription.py")
        logger.warning("and ensure the audio file exists to run a test.")
