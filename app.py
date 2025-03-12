# app.py
from flask import Flask, request, jsonify, send_file
import os
import uuid
import subprocess
import re
import time
import shutil
from urllib.parse import urlparse
import logging
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')
MAX_FILE_AGE = 3600  # Files older than this (in seconds) will be deleted
ALLOWED_FORMATS = ['best', 'bestvideo+bestaudio', 'mp4', 'webm', 'mp3', 'm4a']

# Create download directory if it doesn't exist
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def clean_old_files():
    """Delete files older than MAX_FILE_AGE seconds"""
    current_time = time.time()
    try:
        for filename in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(file_path):
                file_age = current_time - os.path.getmtime(file_path)
                if file_age > MAX_FILE_AGE:
                    os.remove(file_path)
                    logger.info(f"Deleted old file: {filename}")
    except Exception as e:
        logger.error(f"Error cleaning old files: {e}")

def is_valid_url(url):
    """Basic URL validation"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

def sanitize_format(format_option):
    """Ensure the requested format is allowed"""
    if format_option in ALLOWED_FORMATS:
        return format_option
    return 'best'  # Default to best if format is not allowed

def get_output_template(url_domain, format_option):
    """Generate an output template for yt-dlp based on the source and format"""
    unique_id = str(uuid.uuid4())[:8]
    
    # For audio-only formats
    if format_option in ['mp3', 'm4a']:
        return os.path.join(DOWNLOAD_DIR, f"{url_domain}-{unique_id}.%(ext)s")
    
    # For video formats
    return os.path.join(DOWNLOAD_DIR, f"{url_domain}-{unique_id}.%(ext)s")

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/api/download', methods=['POST'])
def download_video():
    # Clean old files first
    clean_old_files()
    
    # Get request data
    data = request.json
    url = data.get('url', '')
    format_option = sanitize_format(data.get('format', 'best'))
    
    # Validate URL
    if not is_valid_url(url):
        return jsonify({'success': False, 'error': 'Invalid URL provided'})
    
    try:
        # Get domain for file naming
        domain = urlparse(url).netloc.replace('www.', '')
        if '.' in domain:
            domain = domain.split('.')[0]
        
        # Use a simpler output template with a fixed extension for predictability
        if format_option == 'mp3':
            output_filename = f"{domain}-{str(uuid.uuid4())[:8]}.mp3"
        elif format_option == 'm4a':
            output_filename = f"{domain}-{str(uuid.uuid4())[:8]}.m4a"
        else:
            # Default to mp4 for video formats for better compatibility
            output_filename = f"{domain}-{str(uuid.uuid4())[:8]}.mp4"
            
        output_path = os.path.join(DOWNLOAD_DIR, output_filename)
        
        # Log for debugging
        logger.info(f"Download requested for URL: {url} with format: {format_option}")
        logger.info(f"Output path: {output_path}")
        
        # Set up yt-dlp command
        cmd = ['yt-dlp']
        
        # Format options
        if format_option == 'mp3':
            cmd.extend(['-x', '--audio-format', 'mp3'])
        elif format_option == 'm4a':
            cmd.extend(['-x', '--audio-format', 'm4a'])
        elif format_option == 'mp4':
            cmd.extend(['-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4'])
        elif format_option != 'best':
            cmd.extend(['-f', format_option])
            
        # Force output format for video to mp4 if not audio-only
        if format_option not in ['mp3', 'm4a']:
            cmd.extend(['--merge-output-format', 'mp4'])
        
        # Add common options
        cmd.extend([
            '--no-playlist',
            '--restrict-filenames',
            '-o', output_path,
            url
        ])
        
        # Execute yt-dlp command
        logger.info(f"Executing command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"yt-dlp error: {result.stderr}")
            return jsonify({
                'success': False, 
                'error': f'Failed to download the video: {result.stderr}'
            })
        
        # Check if file exists after download
        if not os.path.exists(output_path):
            logger.error(f"File wasn't created at expected path: {output_path}")
            
            # Try to find what was actually created
            created_files = []
            for file in os.listdir(DOWNLOAD_DIR):
                if file.startswith(f"{domain}-"):
                    created_files.append(file)
                    
            if created_files:
                # Use the first matching file if any were found
                output_filename = created_files[0]
                output_path = os.path.join(DOWNLOAD_DIR, output_filename)
                logger.info(f"Found alternative file: {output_path}")
            else:
                return jsonify({
                    'success': False,
                    'error': 'File was not created after download'
                })
        
        # Get video title from yt-dlp
        title_cmd = ['yt-dlp', '--get-title', url]
        title_result = subprocess.run(title_cmd, capture_output=True, text=True)
        title = title_result.stdout.strip() if title_result.returncode == 0 else "Video"
        
        # Generate download URL relative to server
        download_url = f"/download/{output_filename}"
        
        logger.info(f"Download ready - Title: {title}, File: {output_filename}")
        
        return jsonify({
            'success': True,
            'title': title,
            'downloadUrl': download_url
        })
            
    except Exception as e:
        logger.exception("Error in download process")
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        })

@app.route('/download/<filename>')
def download_file(filename):
    """Serve the downloaded file"""
    # Security check: ensure filename doesn't contain path traversal
    secure_name = secure_filename(filename)
    file_path = os.path.join(DOWNLOAD_DIR, secure_name)
    
    if not os.path.exists(file_path):
        # Log more information for debugging
        logger.error(f"File not found: {file_path}")
        try:
            logger.error(f"Files in directory: {os.listdir(DOWNLOAD_DIR)}")
        except Exception as e:
            logger.error(f"Error listing directory: {e}")
        
        return jsonify({
            'success': False, 
            'error': f'File not found: {secure_name}'
        }), 404
    
    return send_file(file_path, as_attachment=True)

@app.errorhandler(Exception)
def handle_error(e):
    logger.exception("Unhandled exception")
    return jsonify({'success': False, 'error': 'Server error occurred'}), 500

if __name__ == '__main__':
    # Start with a clean download directory
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
    os.makedirs(DOWNLOAD_DIR)
    
    app.run(host='0.0.0.0', port=5000, debug=True) # Enable debug modegit checkout --
