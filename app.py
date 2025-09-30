import os
import io
from flask import (
    Flask, render_template, request, send_file, session, 
    jsonify, send_from_directory, flash
)
from PIL import Image, ImageFilter
import pytesseract
from fpdf import FPDF
from werkzeug.utils import secure_filename
from pytesseract.pytesseract import TesseractNotFoundError
# --- NEW IMPORT ---
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv() 

# --- Configuration & Tesseract Status Check (FIXED LOGIC) ---

# 1. Use environment variable for the primary Tesseract path
TESSERACT_PRIMARY_PATH = os.environ.get('TESSERACT_CMD') 

# Define fallback paths
TESSERACT_FALLBACK_PATHS = [
    TESSERACT_PRIMARY_PATH, # Check the environment variable first
    # Common defaults as backups
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    # Your specific user path (only kept as a last resort, prefer using TESSERACT_CMD in .env)
    r'C:\Users\Marjory\AppData\Local\Programs\Tesseract-OCR\tesseract.exe', 
]

# Check for Tesseract installation and set the command path
tesseract_found = False
tesseract_status_msg = "Tesseract Not Found. OCR will fail."
final_tesseract_path = None

# Filter out None values and duplicates before checking paths
unique_paths = list(filter(None, set(TESSERACT_FALLBACK_PATHS)))

for path in unique_paths:
    if os.path.exists(path):
        pytesseract.pytesseract.tesseract_cmd = path
        tesseract_found = True
        tesseract_status_msg = f"Tesseract Found. Path: {path}"
        final_tesseract_path = path
        break
        
# --- REFINED LOGIC: Only set tesseract_cmd if found, or if TESSERACT_CMD was specified (to allow TesseractNotFoundError to be raised later) ---
if tesseract_found:
    pytesseract.pytesseract.tesseract_cmd = final_tesseract_path
elif TESSERACT_PRIMARY_PATH:
    # Set the path to the user-specified one, even if it doesn't exist.
    # This allows pytesseract to fail gracefully inside the try/except block.
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PRIMARY_PATH
else:
    # Set a common default path if no path was found anywhere, to prevent the app from halting
    # if pytesseract.pytesseract.tesseract_cmd is accessed before checking tesseract_found.
    # The 'tesseract_found' flag handles the real error messaging.
    pytesseract.pytesseract.tesseract_cmd = 'tesseract'


app = Flask(__name__)
# --- FIX: Use environment variable for secret key ---
app.secret_key = os.environ.get('SECRET_KEY', 'default_secret_fallback_key') 

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'tif', 'tiff', 'pdf'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
MAX_FILE_SIZE = 5 * 1024 * 1024 
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE 
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Helper Function: Advanced Image Preprocessing ---
def preprocess_image(img):
    """Applies image enhancement (grayscale, noise reduction, thresholding)."""
    img = img.convert('L')
    img = img.filter(ImageFilter.MedianFilter(3)) 
    threshold = 180 
    img = img.point(lambda x: 0 if x < threshold else 255, '1')
    return img

# --- Route for the Main Page ---
@app.route('/', methods=['GET'])
def index():
    """Renders the main page, passing preferences and Tesseract status."""
    last_language = session.get('last_language', 'eng')
    last_pdf_title = session.get('last_pdf_title', '')
    
    return render_template('index.html', 
                           last_language=last_language, 
                           last_pdf_title=last_pdf_title,
                           tesseract_status=tesseract_status_msg,
                           tesseract_ok=tesseract_found)

# ------------------------------------------------------------------
# --- Route for Image Upload and OCR (Uses PSM) ---
# ------------------------------------------------------------------
@app.route('/upload', methods=['POST'])
def upload_file():
    """Handles image upload, performs OCR page-by-page, and returns text/filename."""
    if not tesseract_found:
        flash("Tesseract is not configured. OCR cannot run.", 'error')
        return "Tesseract Not Ready", 503

    if 'file' not in request.files:
        flash('No file part in the request.', 'error')
        return 'No file part', 400
    
    file = request.files['file']
    ocr_language = request.form.get('language', 'eng') 
    ocr_psm = request.form.get('psm', '3') # Get PSM from the frontend form data

    if file.filename == '':
        flash('No selected file.', 'error')
        return 'No selected file', 400
    
    # Reset cursor and check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        error_msg = f'File size exceeds the limit of {MAX_FILE_SIZE / (1024 * 1024):.0f} MB.'
        flash(error_msg, 'error')
        return error_msg, 413
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Clean up old files in the upload folder
        for existing_file in os.listdir(app.config['UPLOAD_FOLDER']):
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], existing_file))
            
        file.save(filepath)

        try:
            img = Image.open(filepath)
            full_document_text = []
            
            # --- Tesseract Configuration String (Uses PSM) ---
            tess_config = f'--psm {ocr_psm}'

            for i in range(img.n_frames):
                img.seek(i) 
                current_img = img.copy() 
                current_img = preprocess_image(current_img) 

                # Perform OCR on the single page, applying the PSM configuration
                page_text = pytesseract.image_to_string(current_img, lang=ocr_language, config=tess_config)
                
                full_document_text.append(f"\n--- PAGE {i + 1} ---\n\n" + page_text)

            extracted_text = "\n\n".join(full_document_text)

            session['ocr_text'] = extracted_text
            session['last_language'] = ocr_language 
            
            flash(f"Successfully scanned '{filename}' ({img.n_frames} page(s)).", 'success')
            return jsonify({'text': extracted_text, 'filename': filename}), 200

        except TesseractNotFoundError:
            # This should only happen if the environment variable path was wrong but Tesseract wasn't found in fallback
            error_msg = f"Tesseract not found. Path: {pytesseract.pytesseract.tesseract_cmd}. Check your .env file."
            flash(error_msg, 'error')
            return error_msg, 500 
            
        except Exception as e:
            error_msg = f'OCR failed (Language: {ocr_language}, PSM: {ocr_psm}). Error: {str(e)}'
            flash(error_msg, 'error')
            return error_msg, 500
    
    flash('File type not allowed.', 'error')
    return 'File type not allowed', 400

# ------------------------------------------------------------------
# --- Route for PDF Generation (Uses Font Selection) ---
# ------------------------------------------------------------------
@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    """Takes the edited text and generates a PDF document."""
    edited_text = request.form.get('edited_text', '')
    download_name = request.form.get('download_name', 'scanned_document.pdf')
    pdf_font = request.form.get('pdf_font', 'Arial') # Get PDF Font from the frontend form data

    if not edited_text:
        flash('No text provided for PDF generation.', 'warning')
        return 'No text provided for PDF generation.', 400

    try:
        base_name = download_name.replace('.pdf', '')
        session['last_pdf_title'] = base_name
        
        pdf = FPDF('P', 'mm', 'A4') 
        pdf.add_page()
        # Use the dynamically selected font
        pdf.set_font(pdf_font, size=12)
        
        # Ensure encoding handles common characters for fpdf
        safe_text = edited_text.encode('latin-1', 'replace').decode('latin-1')
        
        pdf.multi_cell(0, 10, safe_text, align='J') 

        pdf_output = pdf.output(dest='S').encode('latin-1')
        
        flash(f"Successfully generated PDF: {download_name} (Font: {pdf_font}).", 'success')
        return send_file(
            io.BytesIO(pdf_output),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=download_name
        )

    except Exception as e:
        error_msg = f'PDF generation failed: {str(e)}'
        flash(error_msg, 'error')
        return error_msg, 500

# --- Serve uploaded files ---
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --- Run the App ---
if __name__ == '__main__':
    app.run(debug=True)