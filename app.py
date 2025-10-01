
import os
import json
import logging
import time
import datetime # For current year in footer
from dotenv import load_dotenv # type: ignore # Used for loading the Flask secret key
from flask import Flask, request, render_template, send_from_directory, flash, make_response
from PIL import Image, UnidentifiedImageError
import pytesseract
from fpdf import FPDF
import fitz  # type: ignore

# Standard library module for threading
import threading

# 🚀 NEW: Import the Limiter extension
from flask_limiter import Limiter # type: ignore
from flask_limiter.util import get_remote_address # type: ignore

# 🚀 NEW: Import libraries for text processing
from langdetect import detect, LangDetectException # type: ignore
from spellchecker import SpellChecker # type: ignore

# --- CONFIGURATION & INITIALIZATION ---

# Load environment variables from .env file (for FLASK_SECRET_KEY, TESSERACT_CMD)
load_dotenv()

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'tif', 'tiff', 'pdf'}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
CLEANUP_AGE_SECONDS = 3600 # 1 hour: files older than this will be deleted

# Tesseract Configuration
TESSERACT_PATH = os.environ.get('TESSERACT_CMD') or 'tesseract'

# Explicitly set pytesseract.tesseract_cmd
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
# Set the secret key from the environment variable for security
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'default_fallback_secret_for_local_testing_only')

# Ensure the upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🚀 NEW: Initialize Flask-Limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"] # Global default limit (optional)
)


# 🚀 NEW: Global SpellChecker instance (loads the dictionary once)
spell = SpellChecker()


# 🚀 NEW: Mock Task Queue storage (in-memory dictionary)
# In a real app, this would be Redis or a database.
task_results = {}
task_lock = threading.Lock()


# --- UTILITIES ---

def allowed_file(filename):
    """Check if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def cleanup_old_files():
    """Deletes files in the upload folder older than CLEANUP_AGE_SECONDS."""
    now = time.time()
    deleted_count = 0

    for filename in os.listdir(app.config['UPLOAD_FOLDER']):
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)

        if os.path.isdir(filepath):
            continue

        file_mod_time = os.path.getmtime(filepath)
        if (now - file_mod_time) > CLEANUP_AGE_SECONDS:
            try:
                os.remove(filepath)
                deleted_count += 1
            except OSError as e:
                logger.error(f"Error deleting old file {filename}: {e}")

    if deleted_count > 0:
        logger.info(f"Cleanup: Deleted {deleted_count} old files.")


def process_text_for_ocr(doc_path):
    """
    Helper to extract a small text sample for language detection.
    This uses a low-res rendering of the first page for speed.
    """
    try:
        ext = os.path.splitext(doc_path)[1].lower()
        if ext in ('.pdf', '.tif', '.tiff'):
            doc = fitz.open(doc_path)
            if doc.page_count > 0:
                page = doc.load_page(0)
                # Render low-res pixmap (e.g., 75 DPI) to save time
                pix = page.get_pixmap(dpi=75)
                pil_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                doc.close()
                return pytesseract.image_to_string(pil_image)
            doc.close()
            return ""
        else:
            return pytesseract.image_to_string(Image.open(doc_path))
    except Exception as e:
        logger.warning(f"Error extracting text sample for detection: {e}")
        return ""


def perform_ocr(image_path, lang, psm):
    """Perform OCR, Language Detection, and Spell Check on the file and return the result."""
    try:
        if not TESSERACT_OK:
            raise pytesseract.TesseractNotFoundError("Tesseract not available.")

        # --- 🚀 STEP 1: Automatic Language Detection ---
        if lang == 'detect':
            sample_text = process_text_for_ocr(image_path)
            if sample_text:
                try:
                    detected_lang = detect(sample_text)
                    lang = 'eng' if detected_lang == 'en' else detected_lang
                    logger.info(f"Language auto-detected as: {lang}")
                except LangDetectException:
                    lang = 'eng'
                    logger.warning("Language detection failed, defaulting to 'eng'")
            else:
                lang = 'eng'

        # Set Tesseract config, including the determined language
        config = f'--oem 3 --psm {psm} -l {lang}'

        # --- STEP 2: Perform OCR (Multi-page handled by fitz) ---
        ext = os.path.splitext(image_path)[1].lower()
        full_text = []

        if ext in ('.pdf', '.tif', '.tiff'):
            doc = fitz.open(image_path)
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=300)
                pil_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                # Perform OCR on this page
                page_text = pytesseract.image_to_string(pil_image, config=config)

                # Add separator and text
                if full_text:
                    full_text.append(
                        "\n======================================================\n"
                        f"--- PAGE {i + 1} of {doc.page_count} ---\n"
                        "======================================================\n\n"
                    )
                full_text.append(page_text)
            doc.close()
            ocr_result = "".join(full_text)
        else:
            # Single-page image
            ocr_result = pytesseract.image_to_string(Image.open(image_path), config=config)

        # --- 🚀 STEP 3: Post-OCR Spell Checking ---
        words = spell.split_words(ocr_result)
        misspelled = spell.unknown(words)

        cleaned_words = []
        for word in words:
            if word in misspelled:
                correction = spell.correction(word)
                cleaned_words.append(correction or word)
            else:
                cleaned_words.append(word)

        final_text = " ".join(cleaned_words).strip()

        # FIX: Corrected typo 'sucess' -> 'success'
        return {'status': 'success', 'text': final_text}

    except pytesseract.TesseractNotFoundError:
        return {'status': 'error', 'message': 'OCR Engine is not running or Tesseract '
        'is not installed correctly.'}
    except RuntimeError as e:
        error_msg = str(e)
        if 'Failed loading language' in error_msg:
            return {'status': 'error', 'message': f"Tesseract language pack '{lang}' is missing."}
        return {'status': 'error', 'message': f'An OCR processing error occurred: {error_msg}'}
    except UnidentifiedImageError:
        return {'status': 'error', 'message': "Could not open file. Ensure it is a valid image/PDF file."}
    except Exception as e:
        return {'status': 'error', 'message': f'An unexpected error occurred during OCR: {e}'}


# 🚀 NEW: Task worker function to run in a separate thread
def ocr_worker(task_id, filepath, lang, psm, original_filename):
    """Executes the long-running OCR task and stores the result."""
    logger.info(f"Task {task_id}: Starting OCR on {original_filename}...")
    
    # Run the complex OCR/detection/spell-check logic
    result = perform_ocr(filepath, lang, psm)

    # Process image preview ONLY if OCR was successful
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
                    # Use lower resolution for faster preview loading
                    pix = page.get_pixmap(dpi=150)
                    pix.save(preview_filepath)
                doc.close()
            except Exception as e:
                logger.warning(f"Could not create preview image for {original_filename}: {e}")
                preview_filename = original_filename

    # Store the final result in the global task storage
    with task_lock:
        task_results[task_id] = {
            'status': 'complete' if result['status'] == 'success' else 'failed',
            'data': result,
            'preview_filename': preview_filename
        }

    logger.info(f"Task {task_id}: Finished.")
    # Clean up the original uploaded file immediately after processing is done
    try:
        os.remove(filepath)
        logger.info(f"Deleted original file: {filepath}")
    except OSError as e:
        logger.error(f"Error deleting temporary file {filepath}: {e}")


# --- FLASK ROUTES ---

@app.before_request
def before_request():
    """Run cleanup before every request."""
    cleanup_old_files()

@app.route('/', methods=['GET'])
def index():
    """Renders the main HTML page."""
    current_year = datetime.date.today().year
    return render_template(
        'index.html',
        tesseract_ok=TESSERACT_OK,
        tesseract_status=TESSERACT_STATUS,
        last_language=request.cookies.get('last_language', 'eng'),
        last_pdf_title=request.cookies.get('last_pdf_title', ''),
        current_year=current_year # Pass current_year to template
    )

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves the uploaded file (for preview in the Document Viewer)."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/upload', methods=['POST'])
# 🚀 Apply a specific rate limit to the high-resource route
@limiter.limit("5 per minute; 30 per hour", override_defaults=True)
def upload_file():
    """
    FIXED: Now starts OCR asynchronously in a new thread and returns a task_id immediately.
    """

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

    if not allowed_file(original_filename):
        flash('File type not allowed.', 'error')
        return json.dumps({'status': 'error', 'message': 'Invalid file type'}), 400

    # --- File Size Check ---
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        flash(f'File size exceeds {MAX_FILE_SIZE / (1024*1024)}MB limit.', 'error')
        return json.dumps({'status': 'error', 'message': 'File too large'}), 413

    # Generate a unique task ID and safe filename
    task_id = os.urandom(16).hex()
    safe_filename = f"{task_id}_{original_filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)

    try:
        file.save(filepath)

        lang = request.form.get('language', 'eng')
        psm = request.form.get('psm', '3')
        pdf_title_base = original_filename.rsplit('.', 1)[0]

        # 🚀 NEW: Place task status into in-memory queue
        with task_lock:
            task_results[task_id] = {'status': 'pending'}

        # 🚀 NEW: Start OCR in a separate thread (mock async worker)
        thread = threading.Thread(
            target=ocr_worker,
            args=(task_id, filepath, lang, psm, original_filename)
        )
        thread.start()

        # Set cookies and return Task ID immediately (202 Accepted)
        response = make_response(json.dumps({'status': 'processing', 'task_id': task_id}), 202)
        response.headers['Content-Type'] = 'application/json'
        response.set_cookie('last_language', lang, max_age=30*24*60*60)
        response.set_cookie('last_pdf_title', pdf_title_base, max_age=30*24*60*60)

        return response

    except Exception as e:
        logger.error(f"Server Error during upload: {e}")
        return json.dumps({'status': 'error', 'message': f'A server processing error occurred: {e}'}), 500


# 🚀 NEW: Route for the frontend to poll for job status
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
        # Flash the error message for the user after polling is complete
        flash(f"OCR Task Failed: {task['data'].get('message', 'Unknown error.')}", 'error')
        # Clean up the task from memory
        with task_lock:
            del task_results[task_id]
        return json.dumps(task), 200 # Return 200 to signal end of polling

    if task['status'] == 'complete':
        # Clean up the task from memory
        with task_lock:
            del task_results[task_id]

        # Success result contains the text and the final preview filename
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

        text_with_markers = edited_text.replace(
            "======================================================\n",
            "---PDF_PAGE_BREAK---"
        )
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