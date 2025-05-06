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
from app.transcription import transcribe_audio, MODEL_NAME
from app.rag import initialize_rag_db, query_rag_db 

main_bp = Blueprint('main', __name__)

# --- Initialize API Clients --- 

try:
    gemini_api_key = os.environ.get('GEMINI_API_KEY')
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        logging.info("Gemini client configured.")
    else:
        logging.warning("GEMINI_API_KEY not found in environment. Gemini API will not be available.")
except Exception as e:
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

# Internal helper to fetch RAG context
def fetch_rag_context_internal(query_text, k=10):
    """Queries the RAG DB and returns relevant documents."""
    try:
        rag_db_path = current_app.config['RAG_DB_PATH']
        retriever = query_rag_db(query_text, rag_db_path, current_app.logger, n_results=k)
        current_app.logger.info(f"Querying RAG DB internally for: '{query_text[:50]}...' with k={k}")
        try:
            results = retriever.invoke(query_text) # Use invoke for LCEL compatibility
            current_app.logger.info(f"Found {len(results)} results from RAG DB via internal call.")
            # Convert Document objects to JSON-serializable dicts immediately
            serializable_results = [
                {"page_content": doc.page_content, "metadata": doc.metadata}
                for doc in results
            ]
            return serializable_results
        except Exception as e:
            current_app.logger.error(f"Error during internal RAG DB query: {e}", exc_info=True)
            return [] # Return empty on error
    except Exception as e:
        current_app.logger.error(f"Error querying RAG database: {e}", exc_info=True)
        return [] # Return empty on error

# Internal helper for summarization
def summarize_text(transcript, user_prompt):
    """Generates a summary using transcript, context, and prompt."""
    current_app.logger.info("Generating summary...")

    # --- Load Contexts --- 
    system_prompt_content = load_external_context()
    business_context_content = get_saved_context().get('business_context', '')
    saved_instructions_content = get_saved_context().get('custom_instructions', '')

    # Combine static contexts for system instruction
    combined_context_for_system = f"Business Context:\n{business_context_content}\n\nSaved Instructions:\n{saved_instructions_content}"
    final_system_prompt = f"{system_prompt_content}\n\n{combined_context_for_system}"

    # --- Fetch RAG Context --- 
    rag_context_results = fetch_rag_context_internal(transcript) # Fetch based on full transcript
    rag_info = ""
    if rag_context_results:
        rag_info = "\n\nRelevant Context from Notes (Retrieved Automatically):\n"
        for i, doc_data in enumerate(rag_context_results):
            source = doc_data['metadata'].get('source', 'Unknown source')
            # Limit context length per document to avoid overly long prompts
            content_snippet = doc_data['page_content'][:500] + ('...' if len(doc_data['page_content']) > 500 else '')
            rag_info += f"- Source: {source}\n  Content: {content_snippet}\n"

    # --- Assemble User Message --- 
    user_message_content = (
        f"Transcription:\n```\n{transcript}\n```\n{rag_info}\n\n" 
        f"Please use the transcription, the retrieved context (if any), and the following specific instructions "
        f"to generate a concise meeting summary:\n{user_prompt if user_prompt else 'Generate a standard meeting summary.'}"
    )

    # --- Call Gemini for Summarization --- 
    try:
        # Use a model suitable for summarization
        summarizer_model = genai.GenerativeModel(
            model_name='gemini-2.5-pro-preview-05-06', # Or gemini-pro 
            system_instruction=final_system_prompt
        )

        current_app.logger.debug(f"Summarizer - Final System Prompt: {final_system_prompt[:500]}...")
        current_app.logger.debug(f"Summarizer - Final User Message: {user_message_content[:500]}...")

        response = summarizer_model.generate_content(user_message_content)

        # Improved response handling based on Gemini API structure
        summary = "Summary generation failed."
        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                summary = ''.join(part.text for part in candidate.content.parts)
                current_app.logger.info("Summary generated successfully.")
            else:
                # Check finish reason if parts are missing
                finish_reason = candidate.finish_reason
                current_app.logger.warning(f"Gemini summary generation issue. Finish Reason: {finish_reason}. Response: {response}")
                summary = f"Summary generation failed or was empty (Finish Reason: {finish_reason})."
                if finish_reason == genai.types.FinishReason.SAFETY:
                    summary += " Content may have been blocked due to safety settings."
                elif finish_reason == genai.types.FinishReason.RECITATION:
                    summary += " Content may have been blocked due to potential recitation."
                # Add other reasons if needed
        else:
            current_app.logger.error(f"Gemini summary response had no candidates. Response: {response}")

        # Clean the summary by removing double quotes
        cleaned_summary = summary.replace('"', '')

        # Return cleaned summary, fetched rag context, and the prompts used
        return cleaned_summary, rag_context_results, final_system_prompt, user_message_content

    except Exception as e:
        current_app.logger.error(f"Error during summarization: {e}", exc_info=True)
        # Return error, fetched rag context, and prompts used
        return f"Error during summarization: {str(e)}", rag_context_results, final_system_prompt, user_message_content


@main_bp.route('/')
def index():
    context = get_saved_context()
    rag_status = current_app.config.get('RAG_STATUS', 'unknown') 
    return render_template('index.html', business_context=context.get('business_context', ''), rag_status=rag_status)

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
    current_app.logger.info("--- Entered /upload route ---")
    if 'file' not in request.files:
        current_app.logger.error("'/upload' request missing 'file' part.")
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    user_prompt = request.form.get('user_prompt', '') # Get prompt from form data
    current_app.logger.info(f"Request Files: {request.files}")
    current_app.logger.info(f"Request Form Data: {request.form}")

    if file.filename == '':
        current_app.logger.warning("'/upload' request received with no selected file.")
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        # Secure filename and create unique ID
        original_filename = secure_filename(file.filename)
        unique_id = uuid.uuid4().hex
        filename_base, file_ext = os.path.splitext(original_filename)
        # Use unique ID in filename to prevent clashes and simplify cleanup
        filename = f"{filename_base}_{unique_id}{file_ext}"
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        current_app.logger.info(f"Received file: {original_filename}, User prompt: '{user_prompt}'")
        current_app.logger.info(f"Saving uploaded file to: {file_path}")

        transcript = None
        summary = None
        rag_context_results = []
        system_prompt_used = ""
        user_message_used = ""

        try:
            # Ensure upload directory exists
            os.makedirs(current_app.config['UPLOAD_FOLDER'], exist_ok=True)
            file.save(file_path)
            current_app.logger.info("File saved successfully.")

            # --- Step 1: Transcribe Audio --- 
            current_app.logger.info(f"Starting transcription for: {file_path}")
            transcript = transcribe_audio(file_path)

            if transcript:
                current_app.logger.info(f"Transcription successful for {filename}")
                
                # --- Step 2: Summarize Text (includes internal RAG fetch) --- 
                summary, rag_context_results, system_prompt_used, user_message_used = summarize_text(transcript, user_prompt)

                # Prepare response
                response_data = {
                    'transcript': transcript,
                    'summary': summary,
                    'rag_context': rag_context_results, # Already serializable
                    'system_prompt': system_prompt_used,
                    'user_message': user_message_used,
                    'filename': filename # Keep filename for potential future use
                }
                return jsonify(response_data), 200
            else:
                # Transcription failed
                current_app.logger.error(f"Transcription failed for {filename}. See transcription module logs.")
                # Return only transcript error
                return jsonify({'error': 'Transcription failed.', 'transcript': None, 'summary': None}), 500

        except Exception as e:
            current_app.logger.error(f"Error during upload/transcription/summarization process for {filename}: {e}", exc_info=True)
            error_message = f"An unexpected error occurred: {str(e)}"
            # Attempt to return partial results if transcription happened
            return jsonify({ 
                'error': error_message, 
                'transcript': transcript, # May be None
                'summary': None,
                'rag_context': [],
                'filename': filename
            }), 500
        
        finally:
            # --- Step 3: Clean up local file --- 
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    current_app.logger.info(f"Cleaned up temporary file: {file_path}")
                except Exception as e_clean:
                    current_app.logger.error(f"Error cleaning up temporary file {file_path}: {e_clean}")

    else:
        current_app.logger.warning(f"Upload rejected: File type not allowed for '{file.filename}'")
        return jsonify({'error': 'File type not allowed'}), 400

@main_bp.route('/fetch_rag_context', methods=['POST'])
def fetch_rag_context_route():
    data = request.get_json()
    transcript_snippet = data.get('transcript', '')[:500] # Use snippet for direct calls if needed
    current_app.logger.info(f"--- Entered /fetch_rag_context route (Direct Call) ---")
    current_app.logger.info(f"Querying RAG DB at {current_app.config['RAG_DB_PATH']} with transcript snippet: {transcript_snippet}...")

    try:
        # Use the internal fetch function
        results = fetch_rag_context_internal(transcript_snippet)
        current_app.logger.info(f"Found {len(results)} relevant documents via direct call.")
        # Results are already serializable
        return jsonify({'context': results}) # Return structured data

    except Exception as e:
        current_app.logger.error(f"Error querying RAG DB via direct call: {e}", exc_info=True)
        return jsonify({'error': f'Error querying RAG DB: {str(e)}'}), 500

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
