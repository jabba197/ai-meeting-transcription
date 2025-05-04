import logging
from app import create_app

logging.basicConfig(level=logging.INFO)

# Create the app
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5001) # Use port 5001 as specified in Dockerfile
