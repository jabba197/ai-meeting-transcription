import os
import uuid
import json
import logging
import mimetypes
import time
import subprocess
from flask import Blueprint, render_template, request, jsonify, current_app, send_from_directory
from werkzeug.utils import secure_filename
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import google.generativeai

# Create blueprint
main_bp = Blueprint('main', __name__)

# --- Initialize API Clients --- 

# Configure Gemini client
try:
    gemini_api_key = os.environ.get('GEMINI_API_KEY')
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        gemini_client_initialized = True
        logging.info("Gemini client configured.")
    else:
        gemini_client_initialized = False
        logging.warning("GEMINI_API_KEY not found in environment. Gemini API will not be available.")
except Exception as e:
    gemini_client_initialized = False
    logging.error(f"Failed to configure Gemini API: {e}")

# Helper functions
def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'mp3', 'wav', 'ogg', 'm4a', 'flac', 'mp4', 'mpeg', 'mpga', 'aac', 'aiff'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_saved_context():
    context_path = os.path.join(current_app.root_path, 'context.json')
    default_context = {
        'business_context': 'Default business context. Please update this with information about your business, team members, and any specific terminology or context that would help in transcription.',
        'custom_instructions': 'Default custom instructions...'
    }
    if os.path.exists(context_path):
        with open(context_path, 'r') as f:
            try:
                saved_context = json.load(f)
                # Ensure both keys exist, merging defaults if necessary
                return {**default_context, **saved_context} 
            except json.JSONDecodeError:
                logging.error("Error decoding context.json, using defaults.")
                return default_context # Return defaults if file is corrupt
    return default_context # Return defaults if file doesn't exist

def save_context(context_data):
    context_path = os.path.join(current_app.root_path, 'context.json')
    with open(context_path, 'w') as f:
        json.dump(context_data, f)

def load_external_context():
    """Loads context from .md files in the directory specified by CONTEXT_INPUT_PATH."""
    context_input_path = current_app.config.get('CONTEXT_INPUT_PATH')
    external_context = ""
    if context_input_path and os.path.isdir(context_input_path):
        current_app.logger.info(f"Loading external context from: {context_input_path}")
        try:
            for filename in os.listdir(context_input_path):
                if filename.lower().endswith('.md'):
                    file_path = os.path.join(context_input_path, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            external_context += f"\n\n--- Context from {filename} ---\n{f.read()}"
                        current_app.logger.debug(f"Loaded context from: {filename}")
                    except Exception as read_err:
                        current_app.logger.warning(f"Could not read file {filename} in context path: {read_err}")
            if external_context:
                current_app.logger.info("Successfully loaded external context.")
            else:
                current_app.logger.info("No .md files found in the context path.")
        except Exception as list_err:
            current_app.logger.error(f"Error accessing context directory {context_input_path}: {list_err}")
    elif context_input_path:
        current_app.logger.warning(f"CONTEXT_INPUT_PATH ('{context_input_path}') is not a valid directory. Skipping external context.")
    else:
        current_app.logger.info("CONTEXT_INPUT_PATH not configured. Skipping external context.")
    return external_context

# Routes
@main_bp.route('/')
def index():
    context = get_saved_context()
    return render_template('index.html', business_context=context.get('business_context', ''))

@main_bp.route('/save_context', methods=['POST'])
def save_context_route():
    data = request.get_json()
    business_context = data.get('business_context')
    custom_instructions = data.get('custom_instructions') 

    if business_context is None or custom_instructions is None:
        return jsonify({'error': 'Missing business_context or custom_instructions'}), 400

    context_data = {
        'business_context': business_context,
        'custom_instructions': custom_instructions
    }
    
    try:
        save_context(context_data)
        return jsonify({'message': 'Context saved successfully'}), 200
    except Exception as e:
        logging.error(f"Error saving context: {e}")
        return jsonify({'error': f'Failed to save context: {e}'}), 500

@main_bp.route('/upload', methods=['POST'])
def upload():
    current_app.logger.info(f"--- Entered /upload route ---")
    current_app.logger.info(f"Request Files: {request.files}")
    current_app.logger.info(f"Request Form Data: {request.form}")
    
    # Check 1: File part exists?
    if 'file' not in request.files:
        current_app.logger.error("'/upload' request missing 'file' part.")
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    user_prompt = request.form.get('user_prompt', '') # Get the user-specific prompt
    current_app.logger.info(f"Received file: {file.filename}, User prompt: '{user_prompt}'")

    # Check 2: Filename exists?
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        summary = "Could not generate summary."
        original_filename = filename
        error_message = None
        uploaded_file_resource = None # To store the Gemini File object

        try:
            file.save(file_path)
            current_app.logger.info(f"File saved temporarily to {file_path}")

            # --- Gemini Files API Integration ---
            if not gemini_api_key:
                 raise ValueError("GEMINI_API_KEY not configured.")

            current_app.logger.info(f"Uploading {file_path} to Gemini Files API...")
            # Add a small delay before upload, sometimes helps with file system sync
            time.sleep(0.5)
            # Use top-level function genai.upload_file
            uploaded_file_resource = genai.upload_file(path=file_path)
            current_app.logger.info(f"File uploaded successfully. Name: {uploaded_file_resource.name}, URI: {uploaded_file_resource.uri}")

            # Wait for the file to be processed
            current_app.logger.info("Waiting for file processing...")
            file_state = uploaded_file_resource.state.name
            while file_state != 'ACTIVE':
                time.sleep(2) # Wait for 2 seconds before checking again
                # Use top-level function genai.get_file
                uploaded_file_resource = genai.get_file(name=uploaded_file_resource.name)
                file_state = uploaded_file_resource.state.name
                current_app.logger.info(f"File state: {file_state}")
                if file_state == 'FAILED':
                    raise Exception(f"File processing failed for {uploaded_file_resource.name}")
            current_app.logger.info("File is ACTIVE and ready for use.")


            # 3. Summarize directly from Audio using Gemini Files API
            current_app.logger.info("Starting Gemini summarization from audio...")
            context = get_saved_context()
            business_context = context.get('business_context', '')
            custom_instructions = context.get('custom_instructions', '')
            external_context = load_external_context() # Load context from files

            system_prompt = f"""You are an AI assistant specialized in summarizing meeting audio based on provided business context, external documents, and instructions.
            **Business Context:**
            {business_context}

            **External Context from Documents:**
            {external_context if external_context else 'No external context provided.'}

            **Custom Instructions for Summarization:**
            {custom_instructions}
            """
            user_prompt_content = user_prompt if user_prompt else '(No specific request provided, generate a standard concise summary following the custom instructions)'

            # Combine prompt text and the uploaded file resource
            contents = [
                f"""Please analyze the audio content of the provided file and generate an accurate and concise summary, following the provided instructions. Ensure the output uses basic markdown (like bolding key points **like this**, using italics *like this*, and potentially section headers ## Like This ## if appropriate).

                **User's Specific Request for this summary:**
                {user_prompt_content}
                """,
                uploaded_file_resource # Pass the file object directly
            ]
            # Using gemini-2.5-pro-preview-03-25 here as it's often suitable for summarization
            summary_model = genai.GenerativeModel('gemini-2.5-pro-exp-03-25', system_instruction=system_prompt)

            current_app.logger.info("Sending request to Gemini for summarization...")
            summary_response = summary_model.generate_content(contents)
            current_app.logger.info("Received response from Gemini.")

            # Handle potential safety settings block or empty response for summary
            if summary_response.candidates and summary_response.candidates[0].content.parts:
                 summary = summary_response.candidates[0].content.parts[0].text
                 current_app.logger.info("Summary extracted successfully.")
            else:
                current_app.logger.warning(f"Gemini summary response did not contain expected text or was blocked. Response: {summary_response}")
                finish_reason = summary_response.candidates[0].finish_reason.name if summary_response.candidates else 'UNKNOWN'
                if finish_reason == 'SAFETY':
                    summary = "Summary generation blocked due to safety settings."
                    error_message = summary # Set error message for display
                elif finish_reason == 'RECITATION':
                     summary = "Summary generation blocked due to potential recitation."
                     error_message = summary # Set error message for display
                else:
                    summary = f"Summary generation failed or was empty (Finish Reason: {finish_reason})."
                    error_message = summary # Set error message for display

        except Exception as e:
            current_app.logger.error(f"An error occurred during processing {filename}: {e}", exc_info=True)
            error_message = f"An unexpected error occurred: {e}"
            summary = error_message # Display error as summary
        finally:
            # --- Clean up --- # 
            # Delete the file from Gemini service
            if uploaded_file_resource:
                 try:
                     current_app.logger.info(f"Deleting uploaded file {uploaded_file_resource.name} from Gemini service...")
                     # Use top-level function genai.delete_file
                     genai.delete_file(name=uploaded_file_resource.name)
                     current_app.logger.info(f"Successfully deleted {uploaded_file_resource.name}.")
                 except Exception as delete_err:
                     current_app.logger.error(f"Failed to delete file {uploaded_file_resource.name} from Gemini: {delete_err}")

            # Delete the local temporary file
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    current_app.logger.info(f"Deleted local temporary file: {file_path}")
                except Exception as e:
                    current_app.logger.error(f"Error deleting local file {file_path}: {e}")

            # --- Save Summary to File (New Feature) --- 
            if summary and not error_message: # Only save if summary exists and no error occurred
                summary_output_path_config = current_app.config.get('SUMMARY_OUTPUT_PATH')
                if summary_output_path_config:
                    try:
                        # Ensure the output directory exists
                        os.makedirs(summary_output_path_config, exist_ok=True)

                        # Create the output filename (original name + .md)
                        base_filename, _ = os.path.splitext(original_filename)
                        output_filename = f"{base_filename}.md"
                        full_output_path = os.path.join(summary_output_path_config, output_filename)

                        # Write the summary to the file
                        with open(full_output_path, 'w', encoding='utf-8') as f_out:
                            f_out.write(summary)
                        current_app.logger.info(f"Summary successfully saved to: {full_output_path}")

                    except Exception as save_err:
                        current_app.logger.error(f"Failed to save summary to {summary_output_path_config}: {save_err}")
                else:
                    current_app.logger.warning("SUMMARY_OUTPUT_PATH not configured. Skipping saving summary to file.")

        # Prepare data for the results page
        result_data = {
            'summary': summary,
            'filename': original_filename,
            'error': error_message
            # Removed 'transcription' as it's no longer generated separately
        }

        return jsonify(result_data)
    else:
        return jsonify({'error': 'File type not allowed'}), 400

@main_bp.route('/transcribe', methods=['POST'])
def transcribe():
    data = request.get_json()
    filename = data.get('filename')
    user_prompt = data.get('prompt', '')
    include_transcript = data.get('include_transcript', True)

    if not filename:
        return jsonify({'error': 'No filename provided'}), 400

    local_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)

    if not os.path.exists(local_file_path):
        return jsonify({'error': 'Local file not found'}), 404

    transcription = ""
    summary = ""
    gemini_file = None

    try:
        # --- Use Gemini API --- 
        if not gemini_client_initialized:
            raise ValueError("Gemini API client is not initialized. Check API Key.")
        current_app.logger.info("Using Gemini API")

        # 1. Upload file to Gemini Files API
        # Guess MIME type from filename
        mime_type, _ = mimetypes.guess_type(local_file_path)
        if not mime_type:
            # Fallback or raise error if mime type can't be guessed
            # This is less likely due to allowed_file check, but good practice
            file_ext = os.path.splitext(filename)[1].lower()
            # Map common extensions manually if needed, or raise error
            if file_ext == '.mp3': mime_type = 'audio/mpeg' # Note: mimetypes might guess audio/mpeg for mp3
            elif file_ext == '.wav': mime_type = 'audio/wav'
            elif file_ext == '.m4a': mime_type = 'audio/aac' # Explicitly map m4a to aac
            elif file_ext == '.ogg': mime_type = 'audio/ogg' # Add ogg
            elif file_ext == '.flac': mime_type = 'audio/flac' # Add flac
            # Add other mappings based on ALLOWED_EXTENSIONS if necessary
            else: 
                raise ValueError(f"Could not determine MIME type for file: {filename}")
            current_app.logger.warning(f"MIME type guessed as fallback: {mime_type}")
        else:
             current_app.logger.info(f"Guessed MIME type: {mime_type}")

        current_app.logger.info(f"Uploading {filename} ({mime_type}) to Gemini...")
        # Use top-level function genai.upload_file
        gemini_file = genai.upload_file(path=local_file_path, mime_type=mime_type)
        current_app.logger.info(f"Uploaded Gemini file: {gemini_file.name}")

        while gemini_file.state.name == "PROCESSING":
            current_app.logger.info("Waiting for Gemini file processing...")
            time.sleep(2) # Consider making this slightly longer if needed
            # Use top-level function genai.get_file
            gemini_file = genai.get_file(gemini_file.name)

        if gemini_file.state.name == "FAILED":
            raise ValueError("Gemini file processing failed.")

        # 2. Transcribe with Gemini
        current_app.logger.info("Starting Gemini transcription...")
        # Ensure the model name is correct and available
        # Using gemini-1.5-flash for potentially faster transcription
        transcription_model = genai.GenerativeModel('gemini-2.5-pro-preview-03-25') 
        response = transcription_model.generate_content(
            [f"Please transcribe this audio file accurately. Include speaker labels if possible (e.g., Speaker 1:, Speaker 2:).", gemini_file],
            request_options={'timeout': 900} # Increased timeout for potentially long audio
        )
        # Handle potential safety settings block
        if response.candidates and response.candidates[0].content.parts:
            transcription = response.candidates[0].content.parts[0].text
        else:
            # Log the full response if text is missing
            current_app.logger.warning(f"Gemini transcription response did not contain expected text. Response: {response}")
            # Check for finish reason (e.g., safety)
            finish_reason = response.candidates[0].finish_reason if response.candidates else 'UNKNOWN'
            if finish_reason == 'SAFETY':
                 transcription = "Transcription blocked due to safety settings."
            elif finish_reason == 'RECITATION':
                 transcription = "Transcription blocked due to potential recitation."
            else:
                transcription = f"Transcription failed or was empty (Finish Reason: {finish_reason})."

        current_app.logger.info(f"Gemini transcription length: {len(transcription)}")

        # 3. Summarize with Gemini
        if transcription and not transcription.startswith("Transcription blocked") and not transcription.startswith("Transcription failed"):
            current_app.logger.info("Starting Gemini summarization...")
            context = get_saved_context()
            business_context = context.get('business_context', '')
            custom_instructions = context.get('custom_instructions', '')
            external_context = load_external_context() # Load context from files
            system_prompt = f"""You are an AI assistant specialized in summarizing meeting transcripts based on provided business context, external documents, and instructions.
            **Business Context:**
            {business_context}

            **External Context from Documents:**
            {external_context if external_context else 'No external context provided.'}

            **Custom Instructions for Summarization:**
            {custom_instructions}
            """
            user_message = f"""Please summarize the following meeting transcript accurately and concisely, following the provided instructions. Ensure the output uses basic markdown (like bolding key points **like this**, using italics *like this*, and potentially section headers ## Like This ## if appropriate).
            **Transcript:**
            {transcription}

            **User's Specific Request for this summary:**
            {user_prompt if user_prompt else '(No specific request provided, generate a standard concise summary following the custom instructions)'}
            """
            # Use a capable model for summarization, like 1.5 Pro
            summary_model = genai.GenerativeModel('gemini-2.5-pro-preview-03-25', system_instruction=system_prompt)
            summary_response = summary_model.generate_content(user_message)
            
            # Handle potential safety settings block for summary
            if summary_response.candidates and summary_response.candidates[0].content.parts:
                 summary = summary_response.candidates[0].content.parts[0].text
            else:
                current_app.logger.warning(f"Gemini summary response did not contain expected text. Response: {summary_response}")
                finish_reason = summary_response.candidates[0].finish_reason if summary_response.candidates else 'UNKNOWN'
                if finish_reason == 'SAFETY':
                    summary = "Summary blocked due to safety settings."
                elif finish_reason == 'RECITATION':
                    summary = "Summary blocked due to potential recitation."
                else:
                    summary = f"Summarization failed or was empty (Finish Reason: {finish_reason})."

            current_app.logger.info("Gemini summarization complete.")
        elif transcription.startswith("Transcription blocked") or transcription.startswith("Transcription failed"):
            summary = "Summarization skipped because transcription failed or was blocked."
        else:
            summary = "Transcription was empty. No summary generated."

        # --- Construct Response ---
        response_data = {'summary': summary}
        if include_transcript:
            response_data['transcription'] = transcription

        return jsonify(response_data)

    except genai.APIError as e:
        current_app.logger.error(f"Gemini API Error: {e}")
        return jsonify({'error': f'Gemini API Error: {e}'}), 500
    except ValueError as e:
        current_app.logger.error(f"Value Error: {e}")
        if "MIME type" in str(e) or "file processing failed" in str(e):
             return jsonify({'error': f'File processing error: {e}'}), 400
        return jsonify({'error': f'Configuration or Value Error: {e}'}), 500
    except Exception as e:
        current_app.logger.error(f"An unexpected error occurred: {e}", exc_info=True)
        return jsonify({'error': f'An unexpected error occurred: {e}'}), 500
    finally:
        # Clean up Gemini file if created
        if gemini_file:
            try:
                current_app.logger.info(f"Deleting Gemini file: {gemini_file.name}")
                # Use top-level function genai.delete_file
                genai.delete_file(gemini_file.name)
            except Exception as delete_error:
                 current_app.logger.error(f"Error deleting Gemini file {gemini_file.name}: {delete_error}")

        # Clean up local file
        if os.path.exists(local_file_path):
            try:
                os.remove(local_file_path)
                current_app.logger.info(f"Removed local file: {local_file_path}")
            except Exception as remove_error:
                current_app.logger.error(f"Error removing local file {local_file_path}: {remove_error}")

@main_bp.route('/get_context', methods=['GET'])
def get_context_route():
    context = get_saved_context()
    return jsonify(context)
