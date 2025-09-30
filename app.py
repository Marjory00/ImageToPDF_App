import os
import json
import logging
import time 
from dotenv import load_dotenv # type: ignore # Used for loading the Flask secret key
from flask import Flask, request, render_template, send_from_directory, flash, redirect, url_for, make_response
from PIL import Image, UnidentifiedImageError
import pytesseract
from fpdf import FPDF 
import fitz  # type: ignore

# 🚀 NEW: Import the Limiter extension
from flask_limiter import Limiter # type: ignore
from flask_limiter.util import get_remote_address # type: ignore

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
# This uses the IP address to track limits. It defaults to a memory store.
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"] # Global default limit (optional)
)


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

def perform_ocr(image_path, lang, psm):
    """Perform OCR on the file and return the text."""
    try:
        if not TESSERACT_OK:
            raise pytesseract.TesseractNotFoundError("Tesseract not available.")

        config = f'--oem 3 --psm {psm}'
        
        ext = os.path.splitext(image_path)[1].lower()
        if ext in ('.pdf', '.tif', '.tiff'):
            doc = fitz.open(image_path)
            full_text = []
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=300) 
                pil_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(pil_image, lang=lang, config=config)
                
                if full_text:
                    full_text.append(
                        "\n======================================================\n"
                        f"--- PAGE {i + 1} of {doc.page_count} ---\n"
                        "======================================================\n\n"
                    )
                full_text.append(text)
            doc.close()
            text = "".join(full_text)
        else:
            text = pytesseract.image_to_string(Image.open(image_path), lang=lang, config=config)
        
        return text.strip()

    except pytesseract.TesseractNotFoundError as e:
        logger.error(f"Tesseract Error (Not Found): {e}")
        flash('OCR Engine is not running or Tesseract is not installed correctly. Check TESSERACT_CMD.', 'error')
        return None
        
    except RuntimeError as e:
        error_msg = str(e)
        logger.error(f"Tesseract Runtime Error: {error_msg}")
        
        if 'Failed loading language' in error_msg:
            flash(f"OCR failed: Tesseract language pack '{lang}' is missing. Please install it.", 'error')
        elif 'Error opening data file' in error_msg:
            flash("OCR failed: Tesseract data files are missing or inaccessible.", 'error')
        else:
            flash(f'An OCR processing error occurred: {error_msg}', 'error')
        return None
        
    except UnidentifiedImageError as e:
        logger.error(f"Image Error: {e}")
        flash("OCR failed: Could not open file. Ensure it is a valid image/PDF file.", 'error') 
        return None

    except Exception as e:
        logger.error(f"Unclassified OCR Processing Error: {e}")
        flash(f'An unexpected error occurred during OCR: {e}', 'error')
        return None


# --- FLASK ROUTES ---

@app.before_request
def before_request():
    """Run cleanup before every request."""
    cleanup_old_files()

@app.route('/', methods=['GET'])
def index():
    """Renders the main HTML page."""
    return render_template(
        'index.html',
        tesseract_ok=TESSERACT_OK,
        tesseract_status=TESSERACT_STATUS,
        last_language=request.cookies.get('last_language', 'eng'),
        last_pdf_title=request.cookies.get('last_pdf_title', '')
    )

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serves the uploaded file (for preview in the Document Viewer)."""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/upload', methods=['POST'])
# 🚀 NEW: Apply a specific rate limit to the high-resource route
@limiter.limit("5 per minute; 30 per hour", override_defaults=True)
def upload_file():
    """Handles file upload, performs OCR, and prepares the image preview."""
    # ... (File handling and OCR logic remains the same) ...
    
    if not TESSERACT_OK:
        flash('OCR failed: Tesseract is not installed or configured.', 'error')
        return "Tesseract Not Ready", 503

    if 'file' not in request.files:
        flash('No file part in the request.', 'error')
        return "No file part", 400

    file = request.files['file']
    filename = file.filename
    
    if filename == '':
        flash('No selected file.', 'error')
        return "No selected file", 400

    if not allowed_file(filename):
        flash('File type not allowed.', 'error')
        return "Invalid file type", 400

    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    
    if file_size > MAX_FILE_SIZE:
        flash(f'File size exceeds {MAX_FILE_SIZE / (1024*1024)}MB limit.', 'error')
        return "File too large", 413

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        file.save(filepath)
        
        lang = request.form.get('language', 'eng')
        psm = request.form.get('psm', '3')
        
        ocr_text = perform_ocr(filepath, lang, psm)

        if ocr_text is None:
            return "OCR Processing Failed", 500

        # --- Image Preview Logic (First Page Only) ---
        preview_filename = filename
        is_multipage = filename.lower().endswith(('.pdf', '.tif', '.tiff'))
        
        if is_multipage:
            unique_id = os.urandom(8).hex()
            preview_filename = f"{filename.rsplit('.', 1)[0]}_{unique_id}.png"
            preview_filepath = os.path.join(app.config['UPLOAD_FOLDER'], preview_filename)
            
            try:
                doc = fitz.open(filepath)
                if doc.page_count > 0:
                    page = doc.load_page(0)
                    pix = page.get_pixmap(dpi=150)
                    pix.save(preview_filepath)
                doc.close()
            
            except Exception as e:
                logger.warning(f"Could not create preview image for {filename} using fitz: {e}")
                preview_filename = filename 
        
        pdf_title_base = filename.rsplit('.', 1)[0]
        
        response_data = {
            'status': 'success', 
            'text': ocr_text, 
            'filename': preview_filename 
        }
        response = make_response(json.dumps(response_data), 200)
        response.headers['Content-Type'] = 'application/json'
        
        response.set_cookie('last_language', lang, max_age=30*24*60*60)
        response.set_cookie('last_pdf_title', pdf_title_base, max_age=30*24*60*60)
        
        return response

    except Exception as e:
        logger.error(f"Server Error during upload: {e}")
        flash(f'A server processing error occurred: {e}', 'error')
        return "Server Error", 500


# The /generate_pdf route does not require an update as it is low-resource
@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    # ... (PDF generation logic remains the same) ...
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