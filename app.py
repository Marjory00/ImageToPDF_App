import os
import json
import logging
import datetime
from dotenv import load_dotenv # type: ignore
from flask import Flask, request, render_template, send_from_directory, flash, make_response
from PIL import Image, UnidentifiedImageError # Still needed for error handling in the worker
import pytesseract
from fpdf import FPDF
import fitz   # type: ignore

import threading
from flask_limiter import Limiter # type: ignore
from flask_limiter.util import get_remote_address # type: ignore

# 🚀 NEW: Import everything from utils
import utils 


# --- CONFIGURATION & INITIALIZATION ---

load_dotenv() 

# Configuration Constants
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'tif', 'tiff', 'pdf'}
MAX_FILE_SIZE = 5 * 1024 * 1024  
CLEANUP_AGE_SECONDS = 3600 

# Tesseract Configuration
TESSERACT_PATH = os.environ.get('TESSERACT_CMD') or 'tesseract'
if TESSERACT_PATH != 'tesseract':
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

try:
    pytesseract.get_tesseract_version()
    TESSERACT_OK = True
    TESSERACT_STATUS = f"Tesseract found at: {pytesseract.pytesseract.tesseract_cmd}"
except pytesseract.TesseractNotFoundError:
    TESSERACT_OK = False
    TESSERACT_STATUS = "Tesseract not found. Please install it or set the TESSERACT_CMD environment variable."

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'default_fallback_secret_for_local_testing_only') 

# Pass configs to the utils module
utils.configure_utils({
    'ALLOWED_EXTENSIONS': ALLOWED_EXTENSIONS, 
    'UPLOAD_FOLDER': UPLOAD_FOLDER, 
    'CLEANUP_AGE_SECONDS': CLEANUP_AGE_SECONDS,
    'MAX_FILE_SIZE': MAX_FILE_SIZE
    }, TESSERACT_PATH, TESSERACT_OK)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Mock Task Queue storage (in-memory dictionary)
task_results = {} 
task_lock = threading.Lock()


# --- TASK WORKER ---

def ocr_worker(task_id, filepath, lang, psm, original_filename):
    """Executes the long-running OCR task and stores the result."""
    logger.info(f"Task {task_id}: Starting OCR on {original_filename}...")
    
    # Use the utility function for the heavy lifting
    result = utils.perform_ocr(filepath, lang, psm)
    
    preview_filename = original_filename
    if result['status'] == 'success':
        is_multipage = original_filename.lower().endswith(('.pdf', '.tif', '.tiff'))
        if is_multipage:
            try:
                unique_id = os.urandom(8).hex()
                preview_filename = f"{original_filename.rsplit('.', 1)[0]}_{unique_id}.png"
                preview_filepath = os.path.join(app.config['UPLOAD_FOLDER'], preview_filename)
                
                doc = fitz.open(filepath)
                if doc.page_count > 0:
                    page = doc.load_page(0)
                    pix = page.get_pixmap(dpi=150) 
                    pix.save(preview_filepath)
                doc.close()
            except Exception as e:
                logger.warning(f"Could not create preview image for {original_filename}: {e}")
                preview_filename = original_filename 
    
    with task_lock:
        task_results[task_id] = {
            'status': 'complete' if result['status'] == 'success' else 'failed',
            'data': result,
            'preview_filename': preview_filename
        }
    
    logger.info(f"Task {task_id}: Finished.")
    
    # Cleanup: Delete the original uploaded file immediately after processing is done
    utils.delete_file(os.path.basename(filepath))


# --- FLASK ROUTES ---

@app.before_request
def before_request():
    """Run cleanup before every request."""
    # Use the utility function
    utils.cleanup_old_files()

@app.route('/', methods=['GET'])
def index():
    current_year = datetime.date.today().year
    return render_template(
        'index.html',
        tesseract_ok=TESSERACT_OK,
        tesseract_status=TESSERACT_STATUS,
        last_language=request.cookies.get('last_language', 'eng'),
        last_pdf_title=request.cookies.get('last_pdf_title', ''),
        current_year=current_year
    )

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves the uploaded file."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/delete_preview/<filename>', methods=['POST'])
def delete_preview(filename):
    """
    🎯 NEW: Explicit endpoint for client to request deletion of the temporary 
    preview image (e.g., after the user downloads the PDF or hits 'Clear').
    """
    if utils.delete_file(filename):
        return json.dumps({'status': 'ok', 'message': f'File {filename} deleted.'}), 200
    return json.dumps({'status': 'error', 'message': f'File {filename} not found or could not be deleted.'}), 404


@app.route('/upload', methods=['POST'])
@limiter.limit("5 per minute; 30 per hour", override_defaults=True)
def upload_file():
    if not TESSERACT_OK:
        flash('OCR failed: Tesseract is not installed or configured.', 'error')
        return json.dumps({'status': 'error', 'message': 'Tesseract Not Ready'}), 503

    if 'file' not in request.files:
        flash('No file part in the request.', 'error')
        return json.dumps({'status': 'error', 'message': 'No file part'}), 400

    file = request.files['file']
    original_filename = file.filename
    
    if original_filename == '':
        flash('No selected file.', 'error')
        return json.dumps({'status': 'error', 'message': 'No selected file'}), 400

    # Use the utility function
    if not utils.allowed_file(original_filename):
        flash('File type not allowed.', 'error')
        return json.dumps({'status': 'error', 'message': 'Invalid file type'}), 400

    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    if file_size > MAX_FILE_SIZE:
        flash(f'File size exceeds {MAX_FILE_SIZE / (1024*1024)}MB limit.', 'error')
        return json.dumps({'status': 'error', 'message': 'File too large'}), 413

    task_id = os.urandom(16).hex()
    safe_filename = f"{task_id}_{original_filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
    
    try:
        file.save(filepath)
        
        lang = request.form.get('language', 'eng')
        psm = request.form.get('psm', '3')
        pdf_title_base = original_filename.rsplit('.', 1)[0]
        
        with task_lock:
            task_results[task_id] = {'status': 'pending'}

        thread = threading.Thread(
            target=ocr_worker, 
            args=(task_id, filepath, lang, psm, original_filename)
        )
        thread.start()

        response = make_response(json.dumps({'status': 'processing', 'task_id': task_id}), 202)
        response.headers['Content-Type'] = 'application/json'
        response.set_cookie('last_language', lang, max_age=30*24*60*60)
        response.set_cookie('last_pdf_title', pdf_title_base, max_age=30*24*60*60)
        
        return response 

    except Exception as e:
        logger.error(f"Server Error during upload: {e}")
        return json.dumps({'status': 'error', 'message': f'A server processing error occurred: {e}'}), 500


@app.route('/status/<task_id>', methods=['GET'])
def get_status(task_id):
    """Returns the status and result of the asynchronous OCR task."""
    with task_lock:
        task = task_results.get(task_id)

    if not task:
        return json.dumps({'status': 'error', 'message': 'Task ID not found.'}), 404

    if task['status'] == 'pending':
        return json.dumps({'status': 'processing'}), 200
    
    if task['status'] == 'failed':
        flash(f"OCR Task Failed: {task['data'].get('message', 'Unknown error.')}", 'error')
        # Clean up the task from memory once requested
        with task_lock:
             del task_results[task_id]
        return json.dumps(task), 200 
    
    if task['status'] == 'complete':
        # Clean up the task from memory once requested
        with task_lock:
             del task_results[task_id]
        
        response_data = {
            'status': 'success',
            'text': task['data']['text'],
            'filename': task['preview_filename']
        }
        return json.dumps(response_data), 200

    return json.dumps({'status': 'error', 'message': 'Unknown task state.'}), 500


@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    edited_text = request.form.get('edited_text')
    download_name = request.form.get('download_name', 'scanned_document.pdf')
    pdf_font = request.form.get('pdf_font', 'Arial')

    if not edited_text:
        return "No text provided for PDF generation.", 400

    try:
        font_map = {'Times': 'Times', 'Courier': 'Courier', 'Arial': 'Helvetica'}
        font_name = font_map.get(pdf_font, 'Helvetica')
        
        pdf = FPDF(unit='mm', format='A4')
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.set_font(font_name, size=12)

        # Use the page break markers for multi-page output
        text_with_markers = edited_text.replace(
            "======================================================\n",
            "---PDF_PAGE_BREAK---"
        )
        # Remove the first marker if it appears at the very start
        text_with_markers = text_with_markers.replace("---PDF_PAGE_BREAK---", "", 1)
        
        text_blocks = text_with_markers.split("---PDF_PAGE_BREAK---")

        if len(text_blocks) == 1 and text_blocks[0].strip() == "":
            text_blocks = [edited_text] 
        
        for block_content in text_blocks:
            block = block_content.strip()
            if not block:
                continue

            pdf.add_page()
            
            lines = block.split('\n', 1)
            
            # Check for the page header marker added by the OCR worker
            if '--- PAGE' in lines[0] or '--- APPENDED PAGE' in lines[0]:
                header = lines[0].strip()
                content = lines[1].strip() if len(lines) > 1 else ''
            else:
                header = ''
                content = block

            if header:
                pdf.set_font(font_name, 'B', 12) 
                pdf.cell(0, 10, header, 0, 1, 'C')
                pdf.set_font(font_name, size=12)
                pdf.ln(2) 

            if content:
                # 🎯 IMPROVEMENT: Use the original content text which should have better line breaks 
                # than the spell-corrected block for better PDF formatting.
                pdf.multi_cell(0, 5, content) 

        pdf_output = pdf.output(dest='S').encode('latin-1')
        
        response = make_response(pdf_output)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename="{download_name}"'

        response.set_cookie('last_pdf_title', download_name.replace('.pdf', ''), max_age=30*24*60*60)

        return response

    except Exception as e:
        logger.error(f"PDF Generation Error: {e}")
        flash('PDF creation failed. Please check the console for details.', 'error')
        return "PDF Generation Failed", 500

# --- RUN THE APP ---
if __name__ == '__main__':
    app.run(debug=True, port=5000)