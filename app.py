import os
import io
import pytesseract
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_file
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from werkzeug.utils import secure_filename

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'tif', 'tiff'}
MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB limit

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = MAX_CONTENT_LENGTH
app.secret_key = 'your_strong_secret_key'

# 🛑 FIX 1: Robust Tesseract Path Check (Windows/Linux/Cloud)
try:
    # ⚠️ 1. Set this to the exact path of tesseract.exe if running locally on Windows!
    WINDOWS_TESSERACT_PATH = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    LINUX_TESSERACT_PATH = '/usr/bin/tesseract'
    
    # Check for the Windows path first
    if os.path.exists(WINDOWS_TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = WINDOWS_TESSERACT_PATH
    # Check for the Linux/Cloud path
    elif os.path.exists(LINUX_TESSERACT_PATH):
        pytesseract.pytesseract.tesseract_cmd = LINUX_TESSERACT_PATH
    # If neither path is found, rely on environment variables/auto-detection
    else:
        pass 
except Exception:
    pass 

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_tesseract_status():
    """Checks if Tesseract is installed and available."""
    try:
        pytesseract.get_tesseract_version()
        return True, "Tesseract is installed and ready."
    except pytesseract.TesseractNotFoundError:
        return False, "Tesseract is NOT installed or path is incorrect."
    except Exception as e:
        return False, f"Tesseract check failed: {str(e)}"

# --- Routes ---

@app.route('/')
def index():
    tesseract_ok, tesseract_status = get_tesseract_status()
    # Placeholder for last language/title, normally stored in session or config
    last_language = request.cookies.get('last_lang', 'eng')
    last_pdf_title = request.cookies.get('last_title', 'Untitled_Document')

    return render_template('index.html', 
                           tesseract_ok=tesseract_ok, 
                           tesseract_status=tesseract_status,
                           last_language=last_language,
                           last_pdf_title=last_pdf_title)


@app.route('/upload', methods=['POST'])
def upload_file():
    tesseract_ok, tesseract_status = get_tesseract_status()
    if not tesseract_ok:
        return jsonify({"error": f"OCR Engine Error: {tesseract_status}"}), 500

    if 'file' not in request.files:
        return jsonify({"error": "No file part in the request."}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        try:
            file.save(filepath)
            
            lang = request.form.get('language', 'eng')
            config = f'--psm {request.form.get("psm", "3")}'
            
            text = pytesseract.image_to_string(filepath, lang=lang, config=config)
            
            # Clean up: Delete the file immediately after use
            os.remove(filepath)
            
            return jsonify({"text": text, "filename": filename}), 200
        
        except pytesseract.TesseractNotFoundError:
            os.remove(filepath) if os.path.exists(filepath) else None
            return jsonify({"error": "Tesseract not installed on server (Check Dockerfile)."}), 500
        except Exception as e:
            os.remove(filepath) if os.path.exists(filepath) else None
            return jsonify({"error": f"OCR Processing Failed: {str(e)}"}), 500
    
    return jsonify({"error": "Invalid file type or upload error."}), 400

@app.route('/generate_pdf', methods=['POST'])
def generate_pdf():
    """
    Generates a PDF from the edited text and streams it to the client.
    """
    edited_text = request.form.get('edited_text', '')
    download_name = request.form.get('download_name', 'document.pdf')
    
    if not edited_text:
        return jsonify({"error": "No text provided for PDF generation."}), 400

    # 1. Create a buffer to hold the PDF data (in memory)
    buffer = io.BytesIO()
    
    # 2. Configure ReportLab Document
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    
    styles['Normal'].fontName = 'Helvetica'
    styles['Normal'].fontSize = 10
    
    Story = []
    
    # 3. Process the text and format it for PDF
    paragraphs = edited_text.split('\n\n') 

    for para in paragraphs:
        if para.strip(): 
            formatted_text = para.replace('\n', '<br/>') 
            
            p = Paragraph(formatted_text, styles['Normal'])
            Story.append(p)
            
            Story.append(Spacer(1, 0.2 * 10))

    # 4. Build the PDF
    try:
        doc.build(Story)
    except Exception as e:
        app.logger.error(f"ReportLab Build Error: {e}")
        return jsonify({"error": f"PDF generation failed: {str(e)}"}), 500

    # 5. Send the file back to the client
    buffer.seek(0)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=download_name,
        mimetype='application/pdf'
    )

if __name__ == '__main__':
    app.run(debug=True)