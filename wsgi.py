from app import create_app

app = create_app()

if __name__ == '__main__':
    # Note: Gunicorn doesn't use this block, but it's fine to leave for direct running
    app.run(host='0.0.0.0', port=5000, debug=True)
