import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv
import threading # Import threading
import logging # Import logging

# Load environment variables
load_dotenv()

def _initialize_rag_background(app, vault_path, db_path):
    """Function to run RAG initialization in the background."""
    with app.app_context(): # Use app context for logging and config access
        if not vault_path:
            app.config['RAG_STATUS'] = 'amber' # Remains amber if path missing
            app.logger.warning('CONTEXT_INPUT_PATH not set, RAG DB initialization skipped in background thread.')
            return
        
        try:
            from app.rag import initialize_rag_db
            app.logger.info('Starting RAG DB initialization in background thread...')
            # Pass the logger instance to the RAG function
            initialize_rag_db(vault_path, db_path, app.logger)
            app.config['RAG_STATUS'] = 'green' # Set to green on success
            app.logger.info('Background RAG DB initialization successful.')
        except Exception as e:
            app.config['RAG_STATUS'] = 'red' # Set to red on failure
            app.logger.error(f'Background RAG DB initialization failed: {e}', exc_info=True)

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # Increased to 500MB max upload
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    app.config['SUMMARY_OUTPUT_PATH'] = os.environ.get('SUMMARY_OUTPUT_PATH') # Load summary output path
    app.config['CONTEXT_INPUT_PATH'] = os.environ.get('CONTEXT_INPUT_PATH')   # Load context input path

    # Configure RAG DB path and initialize status indicator
    project_root = os.path.dirname(app.root_path)
    rag_db_path = os.path.join(project_root, 'rag_db')
    app.config['RAG_DB_PATH'] = rag_db_path
    vault_path = app.config.get('CONTEXT_INPUT_PATH')
    
    # Set initial status to amber
    app.config['RAG_STATUS'] = 'amber'
    app.logger.info('RAG status initially set to amber.')

    # Start RAG initialization in a background thread
    if vault_path:
        rag_thread = threading.Thread(
            target=_initialize_rag_background, 
            args=(app, vault_path, rag_db_path),
            daemon=True # Set as daemon so it doesn't block app exit
        )
        rag_thread.start()
        app.logger.info('Started RAG DB initialization in background thread.')
    else:
        app.logger.warning('CONTEXT_INPUT_PATH not set, RAG DB background initialization skipped.')
        # Status remains 'amber' indicating it's not ready

    # Ensure the upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Enable CORS
    CORS(app)
    
    # Register blueprints
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    return app
