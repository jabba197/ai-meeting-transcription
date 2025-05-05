import os
import uuid
import json
import logging
import mimetypes 
import time
import subprocess
import google.generativeai as genai
from flask import Blueprint, render_template, request, jsonify, current_app, send_from_directory
from werkzeug.utils import secure_filename
from app.transcription import transcribe_audio
from app.rag import query_rag_db

main_bp = Blueprint('main', __name__)

# --- Initialize API Clients --- 

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

@main_bp.route('/')
def index():
    context = get_saved_context()
    return render_template('index.html', business_context=context.get('business_context', ''), rag_status=current_app.config.get('RAG_STATUS', 'amber'))

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
    
    if 'file' not in request.files:
        current_app.logger.error("'/upload' request missing 'file' part.")
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    user_prompt = request.form.get('user_prompt', '') 
    current_app.logger.info(f"Received file: {file.filename}, User prompt: '{user_prompt}'")

    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_id = uuid.uuid4().hex
        base, ext = os.path.splitext(filename)
        unique_filename = f"{base}_{unique_id}{ext}"
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], unique_filename)
        transcript = None 
        error_message = None

        try:
            current_app.logger.info(f"Saving uploaded file to: {file_path}")
            file.save(file_path)
            current_app.logger.info(f"File saved successfully.")

            current_app.logger.info(f"Starting transcription for: {file_path}")
            transcript = transcribe_audio(file_path) 

            if transcript:
                current_app.logger.info(f"Transcription successful for {unique_filename}")
                
                return jsonify({
                    'message': 'File uploaded and transcribed successfully.',
                    'filename': filename, 
                    'transcript': transcript,
                }), 200
            else:
                current_app.logger.error(f"Transcription failed for {unique_filename}. See transcription module logs.")
                error_message = "Transcription failed. Check server logs for details."
                return jsonify({'error': error_message, 'filename': filename}), 500

        except Exception as e:
            current_app.logger.error(f"Error during upload/transcription process for {filename}: {e}", exc_info=True) 
            error_message = f"An unexpected error occurred: {str(e)}"
            return jsonify({'error': error_message, 'filename': filename}), 500
        
        finally:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    current_app.logger.info(f"Cleaned up temporary file: {file_path}")
                except Exception as e_clean:
                    current_app.logger.error(f"Error cleaning up temporary file {file_path}: {e_clean}")

    else:
        current_app.logger.warning(f"Upload rejected: File type not allowed for '{file.filename}'")
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
        if not gemini_client_initialized:
            raise ValueError("Gemini API client is not initialized. Check API Key.")
        current_app.logger.info("Using Gemini API")

        mime_type, _ = mimetypes.guess_type(local_file_path)
        if not mime_type:
            file_ext = os.path.splitext(filename)[1].lower()
            if file_ext == '.mp3': mime_type = 'audio/mpeg' 
            elif file_ext == '.wav': mime_type = 'audio/wav'
            elif file_ext == '.m4a': mime_type = 'audio/aac' 
            elif file_ext == '.ogg': mime_type = 'audio/ogg' 
            elif file_ext == '.flac': mime_type = 'audio/flac' 
            else: 
                raise ValueError(f"Could not determine MIME type for file: {filename}")
            current_app.logger.warning(f"MIME type guessed as fallback: {mime_type}")
        else:
             current_app.logger.info(f"Guessed MIME type: {mime_type}")

        current_app.logger.info(f"Uploading {filename} ({mime_type}) to Gemini...")
        gemini_file = genai.upload_file(path=local_file_path, mime_type=mime_type)
        current_app.logger.info(f"Uploaded Gemini file: {gemini_file.name}")

        while gemini_file.state.name == "PROCESSING":
            current_app.logger.info("Waiting for Gemini file processing...")
            time.sleep(2) 
            gemini_file = genai.get_file(gemini_file.name)

        if gemini_file.state.name == "FAILED":
            raise ValueError("Gemini file processing failed.")

        transcription_model = genai.GenerativeModel('gemini-2.5-pro-preview-03-25') 
        response = transcription_model.generate_content(
            [f"Please transcribe this audio file accurately. Include speaker labels if possible (e.g., Speaker 1:, Speaker 2:).", gemini_file],
            request_options={'timeout': 900} 
        )
        if response.candidates and response.candidates[0].content.parts:
            transcription = response.candidates[0].content.parts[0].text
        else:
            current_app.logger.warning(f"Gemini transcription response did not contain expected text. Response: {response}")
            finish_reason = response.candidates[0].finish_reason if response.candidates else 'UNKNOWN'
            if finish_reason == 'SAFETY':
                 transcription = "Transcription blocked due to safety settings."
            elif finish_reason == 'RECITATION':
                 transcription = "Transcription blocked due to potential recitation."
            else:
                transcription = f"Transcription failed or was empty (Finish Reason: {finish_reason})."

        if not transcription:
             current_app.logger.warning(f"Transcription returned None for {local_file_path}")
             # Clean up uploaded file
             if os.path.exists(local_file_path):
                 os.remove(local_file_path)
                 current_app.logger.info(f"Cleaned up uploaded file: {local_file_path}")
             return jsonify({'error': 'Transcription failed (returned None). Check logs.'}), 500

        # Check for transcription failure/blocking *before* attempting RAG/summarization
        if transcription.startswith("Transcription blocked") or transcription.startswith("Transcription failed"):
            current_app.logger.warning(f"Transcription issue for {local_file_path}: {transcription}")
            # Clean up uploaded file
            if os.path.exists(local_file_path):
                os.remove(local_file_path)
                current_app.logger.info(f"Cleaned up uploaded file: {local_file_path}")
            # Return only the transcription error
            return jsonify({'transcription': transcription, 'summary': 'Summarization skipped due to transcription issue.'}), 200 # Return 200 OK but indicate issue

        # If transcription succeeded, proceed with RAG and Summarization
        current_app.logger.info(f"Gemini transcription length: {len(transcription)}")
        summary = "Summarization did not run or failed." # Default summary
        output_filename = None # Default output filename

        try: # Wrap RAG and Summarization in a try block
            # --- RAG Integration ---
            rag_context_string = "No relevant information found in knowledge base."
            rag_db_path = current_app.config.get('RAG_DB_PATH')
            rag_status = current_app.config.get('RAG_STATUS', 'amber')

            if rag_status == 'green' and rag_db_path:
                try:
                    current_app.logger.info("Querying RAG database...")
                    rag_results = query_rag_db(
                        query_text=transcription, # Use transcription as query
                        db_path=rag_db_path,
                        logger=current_app.logger,
                        n_results=3 # Fetch top 3 results
                    )
                    if rag_results:
                        # Format results (assuming rag_results is a list of strings or Document objects)
                        formatted_results = []
                        for i, result in enumerate(rag_results):
                            # Adjust formatting based on what query_rag_db returns
                            if hasattr(result, 'page_content'): # Handle LangChain Document objects
                                 source = result.metadata.get('source', 'Unknown source')
                                 formatted_results.append(f"Source: {os.path.basename(source)}\nContent: {result.page_content}")
                            elif isinstance(result, str):
                                 formatted_results.append(result)
                            else: # Fallback for unknown format
                                 formatted_results.append(str(result))

                        rag_context_string = "\n\n---\n\n".join(formatted_results)
                        current_app.logger.info("Successfully retrieved context from RAG DB.")
                    else:
                        current_app.logger.info("RAG DB query returned no results.")
                except Exception as rag_e:
                    current_app.logger.error(f"Error querying RAG database: {rag_e}", exc_info=True)
                    rag_context_string = "Error retrieving information from knowledge base."
            elif rag_status != 'green':
                 current_app.logger.warning(f"RAG DB status is '{rag_status}', skipping query.")
                 rag_context_string = f"Knowledge base status: {rag_status}. Query skipped."
            else:
                 current_app.logger.warning("RAG DB path not configured, skipping query.")
                 rag_context_string = "Knowledge base path not configured. Query skipped."
            # --- End RAG Integration ---


            # --- Build Summarization Prompt ---
            context = get_saved_context()
            business_context = context.get('business_context', '')
            custom_instructions = context.get('custom_instructions', '')
            external_context = load_external_context() 
            system_prompt = f"""You are an AI assistant specialized in summarizing meeting transcripts based on provided business context, external documents, relevant knowledge base information, and instructions.
            **Business Context:**
            {business_context}

            **External Context from Documents:**
            {external_context if external_context else 'No external context provided.'}

            **Relevant Information from Knowledge Base:**
            {rag_context_string}

            **Custom Instructions for Summarization:**
            {custom_instructions}
            """
            user_message = f"""Please summarize the following meeting transcript accurately and concisely, following the provided instructions. Ensure the output uses basic markdown (like bolding key points **like this**, using italics *like this*, and potentially section headers ## Like This ## if appropriate).
            **Transcript:**
            {transcription}

            **User's Specific Request for this summary:**
            {user_prompt if user_prompt else '(No specific request provided, generate a standard concise summary following the custom instructions)'}
            """
            summary_model = genai.GenerativeModel('gemini-2.5-pro-preview-03-25', system_instruction=system_prompt)
            summary_response = summary_model.generate_content(user_message)

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
            # --- End Call Summarization Model ---
            current_app.logger.info("Gemini summarization complete.")

            # --- Save Summary (Optional) ---
            summary_output_path = current_app.config.get('SUMMARY_OUTPUT_PATH')

            if summary_output_path and filename and not summary.startswith("Summary blocked") and not summary.startswith("Summarization failed"):
                 if not os.path.exists(summary_output_path):
                     os.makedirs(summary_output_path)
                     current_app.logger.info(f"Created summary output directory: {summary_output_path}")
                 base_filename = os.path.splitext(filename)[0]
                 output_filename = os.path.join(summary_output_path, f"{base_filename}_summary_{uuid.uuid4()}.md")
                 try:
                     with open(output_filename, 'w', encoding='utf-8') as f:
                         f.write(f"# Summary for: {filename}\n\n")
                         f.write(summary)
                     current_app.logger.info(f"Summary saved to: {output_filename}")
                 except Exception as save_err:
                     current_app.logger.error(f"Failed to save summary to {output_filename}: {save_err}")
                     output_filename = None # Reset if save fails
            elif not summary_output_path:
                 current_app.logger.info("SUMMARY_OUTPUT_PATH not configured. Summary not saved to file.")
            # --- End Save Summary ---

        except Exception as process_err: # Catch errors during RAG/Summarization/Saving
            current_app.logger.error(f"Error during RAG/Summarization/Saving: {process_err}", exc_info=True)
            summary = "An error occurred during summarization processing. Check logs." # Update summary on error

        # Clean up uploaded file (always happens after processing attempt)
        if os.path.exists(local_file_path):
            os.remove(local_file_path)
            current_app.logger.info(f"Cleaned up uploaded file: {local_file_path}")

        # Return successful transcription and the resulting summary (even if summary failed)
        return jsonify({
            'transcription': transcription,
            'summary': summary,
            'summary_filename': output_filename, # Return path if saved, else None
            'system_prompt': system_prompt,   # Add system prompt to response
            'user_message': user_message      # Add user message to response
        })

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
        if gemini_file:
            try:
                current_app.logger.info(f"Deleting Gemini file: {gemini_file.name}")
                genai.delete_file(gemini_file.name)
            except Exception as delete_error:
                 current_app.logger.error(f"Error deleting Gemini file {gemini_file.name}: {delete_error}")

        if os.path.exists(local_file_path):
            try:
                os.remove(local_file_path)
                current_app.logger.info(f"Removed local file: {local_file_path}")
            except Exception as remove_error:
                current_app.logger.error(f"Error removing local file {local_file_path}: {remove_error}")

@main_bp.route('/get_context', methods=['GET'])
def get_context_route():
    """Endpoint to fetch the current context."""
    context = get_saved_context()
    rag_status = current_app.config.get('RAG_STATUS', 'unknown')
    response_data = {
        **context,
        'rag_status': rag_status
    }
    return jsonify(response_data)
