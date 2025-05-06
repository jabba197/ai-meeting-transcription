import os
import uuid
import json
import logging
import mimetypes 
import time
import subprocess
import google.generativeai as genai
from flask import Blueprint, render_template, request, jsonify, current_app, send_from_directory, Response, stream_with_context
from werkzeug.utils import secure_filename
from app.transcription import transcribe_audio, MODEL_NAME
from app.rag import initialize_rag_db, query_rag_db 
import re

main_bp = Blueprint('main', __name__)

# Temporary in-memory store for active tasks
# In a production scenario, use Redis, a database, or another persistent task queue
tasks_in_progress = {} 

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
        current_app.logger.info(f"Querying RAG DB internally for: '{query_text[:50]}...' with k={k}")
        # query_rag_db directly returns the list of document objects
        document_results = query_rag_db(query_text, rag_db_path, current_app.logger, n_results=k)

        current_app.logger.info(f"Found {len(document_results)} results from RAG DB via internal call.")

        # Convert Document objects to JSON-serializable dicts immediately
        serializable_results = [
            {"page_content": doc.page_content, "metadata": doc.metadata}
            for doc in document_results
        ]
        return serializable_results

    except Exception as e:
        current_app.logger.error(f"Error querying RAG database: {e}", exc_info=True)
        return [] # Return empty on error

# New helper function to generate RAG keywords
from typing import Optional, Tuple

def generate_rag_keywords(transcript_text: str, logger: logging.Logger) -> Tuple[Optional[str], str]:
    """
    Generates keywords/queries from transcript text for RAG lookup using Gemini Flash.
    Returns a tuple: (keywords_string, model_name).
    Returns (None, model_name) on error.
    """
    MODEL_NAME = "gemini-2.0-flash"
    logger.info(f"Generating RAG keywords using {MODEL_NAME}...")
    try:
        model = genai.GenerativeModel(MODEL_NAME)
        prompt = (
            "Based on the following meeting transcript, please extract 3-5 key topics or concise search queries. "
            "These should be suitable for retrieving relevant documents from a knowledge base. "
            "Present them as a single comma-separated string of keywords/queries. If no specific topics stand out, return an empty string.\n\n"
            f"Transcript:\n```\n{transcript_text}\n```\n\n"
            "Keywords/Queries:"
        )
        
        response = model.generate_content(prompt)
        
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            keywords_text = ''.join(part.text for part in response.candidates[0].content.parts).strip()
            # Clean potential prefixes
            keywords_text = re.sub(r"^(Keywords:|Queries:|RAG Keywords:|RAG Queries:)\s*", "", keywords_text, flags=re.IGNORECASE).strip()
            
            # Corrected check: ensure keywords_text is not empty and then check its content for error strings
            if keywords_text and ("error" in keywords_text.lower() or "failed" in keywords_text.lower()):
                logger.error(f"RAG keyword generation model returned an error message in its content: {keywords_text}")
                return None, MODEL_NAME
            elif not keywords_text: # If keywords_text became empty after stripping or due to model response
                logger.warning(f"RAG keywords are empty after processing model response. Finish reason: {candidate.finish_reason if hasattr(candidate, 'finish_reason') else 'Unknown'}")
            logger.info(f"Generated RAG keywords: '{keywords_text}'")
            return keywords_text, MODEL_NAME
        elif response.candidates and response.candidates[0].finish_reason in [genai.types.FinishReason.SAFETY, genai.types.FinishReason.RECITATION]:
            logger.warning(f"RAG keyword generation stopped due to {response.candidates[0].finish_reason}. Returning empty keywords.")
            return "", MODEL_NAME # Return empty if blocked, to avoid downstream errors
        else:
            logger.error(f"Failed to generate RAG keywords. No valid content in response. Response: {response}")
            return None, MODEL_NAME
    except Exception as e:
        logger.error(f"Error generating RAG keywords: {e}", exc_info=True)
        return None, MODEL_NAME

# New multimodal summarization function
def summarize_multimodal_audio_and_text(
    audio_file_path: str, 
    user_prompt: str, 
    rag_context_results: list, # Expects list of dicts from fetch_rag_context_internal
    logger: logging.Logger
) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Generates a summary using the original audio, RAG context, and user prompt 
    with a multimodal Gemini model.
    Returns: (summary, model_name, system_prompt_used, user_message_parts_description)
    Returns (error_string, model_name, system_prompt_used, user_message_parts_description) on error.
    """
    MODEL_NAME = "gemini-2.5-pro-preview-05-06"
    logger.info(f"Generating multimodal summary with {MODEL_NAME} for audio: {audio_file_path}")

    business_context_content = get_saved_context().get('business_context', '')
    saved_instructions_content = get_saved_context().get('custom_instructions', '')
    
    system_prompt_combined = (
        f"Business Context:\n{business_context_content}\n\n"
        f"Saved Instructions:\n{saved_instructions_content}\n\n"
        "You are an expert meeting summarizer. Based on the provided audio and any relevant retrieved context, "
        "generate a concise and accurate summary according to the user's request."
    )

    if not os.path.exists(audio_file_path):
        logger.error(f"Audio file not found at: {audio_file_path}")
        return f"Error: Audio file not found for summarization.", MODEL_NAME, system_prompt_combined, "Audio file missing"

    mime_type, _ = mimetypes.guess_type(audio_file_path)
    if not mime_type or not mime_type.startswith("audio/"):
        logger.warning(f"Could not determine a valid audio MIME type for {audio_file_path}. Detected: {mime_type}. Attempting fallback.")
        if audio_file_path.lower().endswith(".m4a"):
            mime_type = "audio/m4a"
        elif audio_file_path.lower().endswith(".mp3"):
            mime_type = "audio/mpeg"
        elif audio_file_path.lower().endswith((".wav", ".wave")):
            mime_type = "audio/wav"
        elif audio_file_path.lower().endswith(".ogg"):
            mime_type = "audio/ogg"
        else:
            logger.error(f"Unsupported audio file type for summarization based on extension: {audio_file_path}")
            return f"Error: Unsupported audio file type '{os.path.basename(audio_file_path)}'.", MODEL_NAME, system_prompt_combined, "Unsupported audio type"
    
    logger.info(f"Preparing audio part: {audio_file_path} with MIME type: {mime_type}")
    audio_file_part = None
    try:
        audio_file_part = genai.upload_file(path=audio_file_path, mime_type=mime_type)
        logger.info(f"Successfully uploaded audio file {audio_file_part.name} to Gemini for multimodal summarization.")
    except Exception as upload_err:
        logger.error(f"Failed to upload audio file to Gemini: {upload_err}", exc_info=True)
        return f"Error: Failed to prepare audio for summarization.", MODEL_NAME, system_prompt_combined, "Audio upload failed"

    rag_info_string = ""
    if rag_context_results:
        rag_info_string = "\n\nRelevant Context from Notes (Retrieved Automatically):\n"
        for i, doc_data in enumerate(rag_context_results):
            source = doc_data['metadata'].get('source', 'Unknown source')
            content_snippet = doc_data['page_content'][:500] + ('...' if len(doc_data['page_content']) > 500 else '')
            rag_info_string += f"- Source: {source}\n  Content: {content_snippet}\n"

    # Extract and clean up the file name to use as meeting title
    file_name = os.path.basename(audio_file_path)
    # Remove file extension and any UUID prefixes if present
    meeting_title = file_name
    if '_' in meeting_title and meeting_title.count('_') >= 1:
        # Try to remove UUID prefix if it exists (pattern like: def52f945d2a422fab5001ebe85e549a_Meeting_Name.m4a)
        parts = meeting_title.split('_', 1)
        if len(parts[0]) > 30:  # Likely a UUID
            meeting_title = parts[1]
    # Remove file extension
    meeting_title = os.path.splitext(meeting_title)[0]
    # Replace underscores with spaces
    meeting_title = meeting_title.replace('_', ' ')

    prompt_parts = [
        audio_file_part,
        f"Based on the provided audio recording titled \"{meeting_title}\" and the following retrieved context (if any), "
        f"please fulfill the user's request.\n"
        f"{rag_info_string}\n\n"
        f"Meeting Title: {meeting_title}\n\n"
        f"User's Request: {user_prompt if user_prompt else 'Generate a standard meeting summary.'}"
    ]
    
    user_message_parts_description = (
        f"Audio: {os.path.basename(audio_file_path)}, "
        f"RAG Context: {'Yes' if rag_info_string else 'No'}, "
        f"User Prompt: {user_prompt[:100]}..."
    )
    
    try:
        model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            system_instruction=system_prompt_combined
        )
        logger.debug(f"Multimodal Summarizer - System Prompt: {system_prompt_combined[:200]}...")
        logger.debug(f"Multimodal Summarizer - User Message Parts (description): {user_message_parts_description}")
        
        response = model.generate_content(prompt_parts)

        summary = f"Multimodal summary generation failed using {MODEL_NAME}."
        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                summary = ''.join(part.text for part in candidate.content.parts)
                logger.info(f"Multimodal summary generated successfully with {MODEL_NAME}.")
            else:
                finish_reason = candidate.finish_reason
                logger.warning(f"Gemini multimodal summary generation issue with {MODEL_NAME}. Finish Reason: {finish_reason}. Response: {response}")
                summary = f"Summary generation failed or was empty (Finish Reason: {finish_reason}) using {MODEL_NAME}."
                if finish_reason == genai.types.FinishReason.SAFETY:
                    summary += " Content may have been blocked due to safety settings."
                elif finish_reason == genai.types.FinishReason.RECITATION:
                    summary += " Content may have been blocked due to potential recitation."
        else:
            logger.error(f"Gemini multimodal summary response had no candidates with {MODEL_NAME}. Response: {response}")

        cleaned_summary = summary.replace('"', '')
        return cleaned_summary, MODEL_NAME, system_prompt_combined, user_message_parts_description

    except Exception as e:
        logger.error(f"Error during multimodal summarization with {MODEL_NAME}: {e}", exc_info=True)
        return f"Error during multimodal summarization: {str(e)}", MODEL_NAME, system_prompt_combined, user_message_parts_description
    finally:
        if audio_file_part:
            try:
                # Deleting the file after use from Gemini storage.
                genai.delete_file(audio_file_part.name)
                logger.info(f"Successfully deleted uploaded audio file {audio_file_part.name} from Gemini.")
            except Exception as del_e:
                logger.warning(f"Could not delete uploaded audio file {audio_file_part.name} from Gemini: {del_e}")

def save_summary_to_markdown(summary, meeting_title, original_filename, timestamp, rag_keywords=None, logger=None):
    """Save the meeting summary as a markdown file in the SUMMARY_OUTPUT_PATH directory."""
    summary_output_path = current_app.config.get('SUMMARY_OUTPUT_PATH')
    if not summary_output_path:
        logger.warning("SUMMARY_OUTPUT_PATH not set, skipping summary markdown file creation")
        return None
        
    try:
        # Create the directory if it doesn't exist
        if not os.path.exists(summary_output_path):
            os.makedirs(summary_output_path)
            logger.info(f"Created SUMMARY_OUTPUT_PATH directory: {summary_output_path}")

        # Format the filename with timestamp for uniqueness
        clean_title = meeting_title.replace(' ', '_').replace('/', '-').replace('\\', '-')
        markdown_filename = f"{timestamp}_{clean_title}.md"
        markdown_filepath = os.path.join(summary_output_path, markdown_filename)
        
        # Create the markdown content
        content = [f"# Meeting Summary: {meeting_title}\n"]
        content.append(f"*Created:* {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))}\n")
        content.append(f"*Source File:* {original_filename}\n")
        
        if rag_keywords:
            content.append(f"*Keywords:* {rag_keywords}\n")
            
        content.append("## Summary\n")
        content.append(f"{summary}\n")
        
        # Write the markdown file
        with open(markdown_filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content))
        
        logger.info(f"Successfully saved summary to markdown file: {markdown_filepath}")
        return markdown_filepath
    except Exception as e:
        logger.error(f"Error saving summary to markdown file: {e}", exc_info=True)
        return None

def generate_task_events(task_id, user_prompt_from_query):
    logger = current_app.logger
    task_info = tasks_in_progress.get(task_id)

    if not task_info:
        logger.error(f"Task ID {task_id} not found in tasks_in_progress.")
        yield f"data: {json.dumps({'error': 'Invalid or expired task ID. Please try uploading again.', 'progress_percent': 100, 'stage': 'Error'})}\n\n"
        return

    filepath = task_info.get('filepath')
    original_filename = task_info.get('original_filename')
    effective_user_prompt = user_prompt_from_query if user_prompt_from_query is not None else task_info.get('user_prompt', '')
    
    logger.info(f"Starting SSE stream for task {task_id}, file {original_filename}, prompt: '{effective_user_prompt[:50]}...'" )
    
    try:
        tasks_in_progress[task_id]['status'] = 'processing'
        total_start_time = time.time()

        if not os.path.exists(filepath):
            logger.error(f"File not found for task {task_id} at {filepath}.")
            yield f"data: {json.dumps({'error': 'File processing error: File not found. Please re-upload.', 'progress_percent': 100, 'stage': 'Error'})}\n\n"
            return

        # --- 1. Transcription --- 
        yield f"data: {json.dumps({'stage': 'Transcribing audio...', 'progress_percent': 10})}\n\n"
        logger.info(f"Task {task_id}: Starting transcription for {original_filename}")
        transcription_result, trans_time, trans_model_name = transcribe_audio(filepath)
        
        # Better error detection - check for None result or if it's a short string starting with 'Error:'
        is_error = (transcription_result is None or 
                   (isinstance(transcription_result, str) and 
                    len(transcription_result) < 1000 and 
                    transcription_result.strip().lower().startswith(('error:', 'an error'))))
        
        if is_error:
            error_message = transcription_result if transcription_result else "Transcription failed due to an unknown error."
            logger.error(f"Task {task_id}: Transcription failed for {original_filename}. Reason: {error_message}")
            yield f"data: {json.dumps({'error': error_message, 'progress_percent': 100, 'stage': 'Transcription Error'})}\n\n"
            return
        logger.info(f"Task {task_id}: Transcription complete. Time: {trans_time:.2f}s. Transcript length: {len(transcription_result)}")
        yield f"data: {json.dumps({'stage': 'Transcription complete. Generating keywords...', 'progress_percent': 25, 'transcript_preview': transcription_result[:200] + '...'})}\n\n"

        # --- 2. Generate RAG Keywords --- 
        rag_keywords_start_time = time.time()
        yield f"data: {json.dumps({'stage': 'Generating keywords for context retrieval...', 'progress_percent': 30})}\n\n"
        logger.info(f"Task {task_id}: Generating RAG keywords from transcript.")
        rag_keywords, rag_keywords_model_name = generate_rag_keywords(transcription_result, logger)
        rag_keywords_time = time.time() - rag_keywords_start_time
        if rag_keywords is None:
            logger.error(f"Task {task_id}: RAG keyword generation failed.")
            # Proceed without RAG context, or yield an error if critical.
            # For now, we'll proceed and log, summarization will occur without RAG context.
            yield f"data: {json.dumps({'warning': 'Keyword generation failed. Proceeding without enhanced context.', 'progress_percent': 45, 'stage': 'Keyword Generation Failed'})}\n\n"
            rag_context_results = [] # Ensure it's an empty list
        else:
            logger.info(f"Task {task_id}: RAG keywords generated: '{rag_keywords}'. Time: {rag_keywords_time:.2f}s")
            yield f"data: {json.dumps({'stage': 'Keywords generated. Retrieving context...', 'progress_percent': 45})}\n\n"

        # --- 3. Fetch RAG Context --- 
        rag_fetch_start_time = time.time()
        rag_context_results = [] # Initialize to empty list
        if rag_keywords: # Only fetch if keywords were generated
            logger.info(f"Task {task_id}: Fetching RAG context with keywords: '{rag_keywords}'")
            rag_context_results = fetch_rag_context_internal(rag_keywords, k=5) # k=5, adjust as needed
            if not rag_context_results:
                logger.info(f"Task {task_id}: No RAG context found for keywords: '{rag_keywords}'")
                yield f"data: {json.dumps({'stage': 'No specific context found. Preparing summary...', 'progress_percent': 60})}\n\n"
            else:
                logger.info(f"Task {task_id}: RAG context retrieved ({len(rag_context_results)} documents). Preparing summary...")
                yield f"data: {json.dumps({'stage': f'Retrieved {len(rag_context_results)} context snippets. Preparing summary...', 'progress_percent': 60})}\n\n"
        else:
             logger.info(f"Task {task_id}: Skipping RAG context retrieval as no keywords were generated.")
             yield f"data: {json.dumps({'stage': 'Skipping context retrieval (no keywords). Preparing summary...', 'progress_percent': 60})}\n\n"
        rag_fetch_time = time.time() - rag_fetch_start_time

        # --- 4. Multimodal Summarization --- 
        summary_start_time = time.time()
        yield f"data: {json.dumps({'stage': 'Generating multimodal summary...', 'progress_percent': 65})}\n\n"
        logger.info(f"Task {task_id}: Starting multimodal summarization.")
        summary_result, summary_model_name, system_prompt_used, user_message_parts_desc = summarize_multimodal_audio_and_text(
            filepath, 
            effective_user_prompt, 
            rag_context_results, 
            logger
        )
        summary_time = time.time() - summary_start_time

        if summary_result is None or (isinstance(summary_result, str) and ("error" in summary_result.lower() or "failed" in summary_result.lower())):
            logger.error(f"Task {task_id}: Multimodal summarization failed. Reason: {summary_result}")
            yield f"data: {json.dumps({'error': summary_result, 'progress_percent': 100, 'stage': 'Summarization Error'})}\n\n"
            return
        logger.info(f"Task {task_id}: Multimodal summarization complete. Time: {summary_time:.2f}s")
        
        total_processing_time = time.time() - total_start_time
        yield f"data: {json.dumps({'stage': 'Processing complete!', 'progress_percent': 100})}\n\n"

        # --- Prepare final result --- 
        final_data = {
            'transcript': transcription_result,
            'summary': summary_result,
            'rag_context_retrieved': rag_context_results, # Renamed for clarity
            'rag_keywords_generated': rag_keywords if rag_keywords is not None else "",
            'model_info': {
                'transcription_model': trans_model_name,
                'rag_keyword_model': rag_keywords_model_name if rag_keywords is not None else "N/A",
                'summarization_model': summary_model_name,
            },
            'prompts_used': {
                'summarization_system_prompt': system_prompt_used,
                'summarization_user_message_parts': user_message_parts_desc,
                # Consider adding RAG keyword generation prompt if needed for debugging
            },
            'timings': {
                'transcription_seconds': round(trans_time, 2),
                'rag_keyword_generation_seconds': round(rag_keywords_time, 2),
                'rag_context_retrieval_seconds': round(rag_fetch_time, 2),
                'summarization_seconds': round(summary_time, 2),
                'total_processing_seconds': round(total_processing_time, 2)
            },
            'filename': original_filename,
            'progress_percent': 100, # Ensure this is set for the final payload
            'stage': 'Processing complete!' # Ensure this is set for the final payload
        }
        
        # Save summary to markdown file
        if summary_result and not "error" in summary_result.lower():
            timestamp = int(time.time())
            # Extract meeting title from filename, consistent with how it's done in summarize_multimodal_audio_and_text
            file_name = os.path.basename(filepath)
            meeting_title = file_name
            # Try to remove UUID prefix if it exists
            if '_' in meeting_title and meeting_title.count('_') >= 1:
                parts = meeting_title.split('_', 1)
                if len(parts[0]) > 30:  # Likely a UUID
                    meeting_title = parts[1]
            # Remove file extension
            meeting_title = os.path.splitext(meeting_title)[0]
            # Replace underscores with spaces for a more readable title
            meeting_title = meeting_title.replace('_', ' ')
            
            logger.info(f"Saving summary for meeting: {meeting_title}")
            markdown_path = save_summary_to_markdown(
                summary_result, 
                meeting_title, 
                original_filename, 
                timestamp, 
                rag_keywords, 
                logger
            )
            if markdown_path:
                final_data['summary_markdown_path'] = markdown_path
                logger.info(f"Added summary markdown path to response: {markdown_path}")
        
        yield f"data: {json.dumps(final_data)}\n\n"
        logger.info(f"Task {task_id}: Successfully processed and sent final result for {original_filename}.")    

    except Exception as e:
        logger.error(f"Unhandled error during SSE generation for task {task_id}: {e}", exc_info=True)
        yield f"data: {json.dumps({'error': f'An unexpected server error occurred: {str(e)}', 'progress_percent': 100, 'stage': 'Critical Error'})}\n\n"
    finally:
        # Cleanup: remove task from dict and delete file
        if task_id in tasks_in_progress:
            task_to_clean = tasks_in_progress.pop(task_id, None)
            if task_to_clean and task_to_clean.get('filepath') and os.path.exists(task_to_clean['filepath']):
                try:
                    os.remove(task_to_clean['filepath'])
                    logger.info(f"Cleaned up file {task_to_clean['filepath']} for task {task_id}")
                except Exception as e_clean:
                    logger.error(f"Error cleaning up file for task {task_id}: {e_clean}")
            logger.info(f"Task {task_id} removed from tasks_in_progress.")

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

@main_bp.route('/initiate_processing', methods=['POST'])
def initiate_processing_route():
    current_app.logger.info("--- Entered /initiate_processing route ---")
    if 'audio_file' not in request.files:
        current_app.logger.error("No audio_file part in request.files")
        return jsonify({'error': 'No audio file part in the request'}), 400

    audio_file = request.files['audio_file']
    user_prompt = request.form.get('prompt', '') # Get prompt from form data

    if audio_file.filename == '':
        current_app.logger.error("No selected audio file")
        return jsonify({'error': 'No selected audio file'}), 400

    if audio_file and allowed_file(audio_file.filename):
        original_filename = secure_filename(audio_file.filename)
        task_id = uuid.uuid4().hex
        # Save file with task_id prefix to avoid collisions and for easy cleanup
        filename_for_storage = f"{task_id}_{original_filename}"
        upload_folder_path = current_app.config['UPLOAD_FOLDER']
        if not os.path.exists(upload_folder_path):
            os.makedirs(upload_folder_path)
            current_app.logger.info(f"Created upload folder: {upload_folder_path}")

        filepath = os.path.join(upload_folder_path, filename_for_storage)

        try:
            audio_file.save(filepath)
            current_app.logger.info(f"File {filename_for_storage} saved to {filepath} for task {task_id}")

            # Store task details
            tasks_in_progress[task_id] = {
                'filepath': filepath,
                'original_filename': original_filename,
                'user_prompt': user_prompt, # Store prompt from initial POST
                'status': 'pending'
            }
            current_app.logger.info(f"Task {task_id} initiated. Details: {tasks_in_progress[task_id]}")
            return jsonify({'task_id': task_id}), 200
        except Exception as e:
            current_app.logger.error(f"Error saving file or initiating task {task_id}: {e}", exc_info=True)
            return jsonify({'error': f'Failed to save file or initiate task: {str(e)}'}), 500
    else:
        current_app.logger.error(f"File type not allowed: {audio_file.filename}")
        return jsonify({'error': 'File type not allowed'}), 400

@main_bp.route('/stream_progress/<task_id>', methods=['GET'])
def stream_progress_route(task_id):
    user_prompt_from_query = request.args.get('prompt') # Get prompt from query string
    current_app.logger.info(f"--- SSE Connection for /stream_progress/{task_id} --- Prompt from query: '{user_prompt_from_query[:50] if user_prompt_from_query else None }...'")
    return Response(stream_with_context(generate_task_events(task_id, user_prompt_from_query)), mimetype='text/event-stream')

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
