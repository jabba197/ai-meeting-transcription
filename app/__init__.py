import os
from flask import Flask
from flask_cors import CORS
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
    app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # Increased to 500MB max upload
    app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    app.config['SUMMARY_OUTPUT_PATH'] = os.environ.get('SUMMARY_OUTPUT_PATH') # Load summary output path
    app.config['CONTEXT_INPUT_PATH'] = os.environ.get('CONTEXT_INPUT_PATH')   # Load context input path
    
    # Ensure the upload folder exists
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Enable CORS
    CORS(app)
    
    # Register blueprints
    from app.routes import main_bp
    app.register_blueprint(main_bp)
    
    return app
