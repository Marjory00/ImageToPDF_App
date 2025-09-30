import os
from flask import Flask, render_template, request, send_file, session
from PIL import Image
import pytesseract
from fpdf import FPDF
import io
from werkzeug.utils import secure_filename

# Import the specific error for better handling
from pytesseract.pytesseract import TesseractNotFoundError

# --- Configuration ---
# !!! IMPORTANT: UPDATE THIS PATH TO YOUR TESSERACT INSTALLATION !!!
# Define potential Tesseract paths for robustness on Windows
TESSERACT_PATHS = [
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
]

# Set pytesseract path by checking which path exists
tesseract_found = False
for path in TESSERACT_PATHS:
    if os.path.exists(path):
        pytesseract.pytesseract.tesseract_cmd = path
        tesseract_found = True
        break

# If Tesseract wasn't found in common paths, fall back to the primary path 
# (This still relies on the user to ensure Tesseract is installed correctly)
if not tesseract_found:
    print("Warning: Tesseract not automatically found. Please ensure it is installed and the path is correct in app.py.")
    # Default to the primary path, which will likely cause TesseractNotFoundError if wrong.
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATHS[0]


app = Flask(__name__)
# Use a secret key for session management (required for Flask's session)
app.secret_key = 'super_secret_key_for_session' 
# Configure upload folder and allowed extensions
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'tif', 'tiff'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create the uploads directory if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Helper function to check file extension
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Route for the Main Page ---
@app.route('/', methods=['GET'])
def index():
    """Renders the main upload form page."""
    return render_template('index.html')

# --- Route for Image Upload and OCR ---
@app.route('/upload', methods=['POST'])
def upload_file():
    """Handles image upload, performs OCR, and stores text in session."""
    if 'file' not in request.files:
        return 'No file part', 400
    
    file = request.files['file']
    
    if file.filename == '':
        return 'No selected file', 400
    
    if file and allowed_file(file.filename):
        # 1. Save the file securely
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # 2. Perform OCR
        try:
            # Open the image using Pillow (PIL)
            img = Image.open(filepath)
            # Use pytesseract to extract text
            extracted_text = pytesseract.image_to_string(img)
            
            # 3. Store the text in the session for editing
            session['ocr_text'] = extracted_text
            
            # Remove the temporary file
            os.remove(filepath) 

            # Return the extracted text to the frontend for editing
            return extracted_text

        # -----------------------------------------------------------
        # Catch TesseractNotFoundError specifically for a clear message
        # -----------------------------------------------------------
        except TesseractNotFoundError:
            # Note: This path will be the one currently configured in pytesseract.
            error_msg = f"Tesseract not found. Please verify the path in app.py. Current path: {pytesseract.pytesseract.tesseract_cmd}"
            # Return error message with 500 status code
            return error_msg, 500 
            
        except Exception as e:
            # Catch other potential errors (like Pillow image handling errors)
            return f'OCR failed due to an unknown error: {str(e)}', 500
    
    return 'File type not allowed', 400

# --- Route for PDF Generation ---
@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    """Takes the edited text and generates a PDF document."""
    # Get the edited text from the POST request form data
    edited_text = request.form.get('edited_text', '')

    if not edited_text:
        return 'No text provided for PDF generation.', 400

    try:
        # 1. Initialize PDF object
        # 'P' for Portrait, 'mm' for units, 'A4' for page size
        pdf = FPDF('P', 'mm', 'A4') 
        pdf.add_page()
        
        # 2. Set font and size (e.g., Arial, regular, 12pt)
        # Note: fpdf2 requires a standard font or a pre-loaded custom one
        pdf.set_font("Arial", size=12)

        # 3. Write the text to the PDF
        # MultiCell is used to handle line breaks and text wrapping
        # 0=auto width, 10=height of lines, align='J' for Justify
        # The .encode() and .decode() are for robust character handling in fpdf2
        pdf.multi_cell(0, 10, edited_text.encode('latin-1', 'replace').decode('latin-1'), align='J') 

        # 4. Save the PDF to an in-memory buffer
        # 'S' means return as a string/bytes buffer
        pdf_output = pdf.output(dest='S').encode('latin-1')
        
        # 5. Send the file back to the client
        return send_file(
            io.BytesIO(pdf_output),
            mimetype='application/pdf',
            as_attachment=True,
            download_name='scanned_document.pdf'
        )

    except Exception as e:
        return f'PDF generation failed: {str(e)}', 500

# --- Run the App ---
if __name__ == '__main__':
    # Set debug=True for development. Disable in production.
    app.run(debug=True)