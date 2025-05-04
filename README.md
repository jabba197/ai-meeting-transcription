# AI Meeting Transcription App

A simple, self-hosted web application for transcribing and summarizing meeting audio.

## Features

- Upload audio files for transcription
- Generate AI summaries of meeting content
- Customize prompts for specific summary styles
- Configurable context settings for business-specific information
- Docker-based for easy deployment

## Quick Start

### Requirements

- Docker and Docker Compose
- Internet connection (for AI API access)

### Running the App

```bash
# Clone the repository
# Navigate to the project directory
cd ai-meeting-transcription

# Start the application
docker-compose up -d

# The app will be available at http://localhost:5000
```

## Development

This application is designed to be beginner-friendly for Python developers. The main components are:

- Flask web framework
- OpenAI API for transcription and summarization
- Simple HTML/CSS/JavaScript frontend

To modify the application:

1. Make your changes to the Python code, HTML templates, or static assets
2. Rebuild the Docker container: `docker-compose up -d --build`

## Configuration

You'll need to provide your own AI API keys in the `.env` file (create one based on `.env.example`).

## License

MIT
