import os
import logging
import datetime
import pytesseract
from PIL import Image
# FIX: Import timedelta for consistent usage in cleanup logic (not strictly needed 
# but good practice if you were using it for comparisons/calculations)
from datetime import timedelta 

# -----------------------------------------------------
# Global variables (Initialized by configure_utils)
# -----------------------------------------------------
# FIX: Use standard logging.getLogger(__name__) for a proper module logger
logger = logging.getLogger(__name__)

# These variables hold the configuration passed from app.py
CONFIG = {}
TESSERACT_OK = False
# TESSERACT_CMD is now handled internally by pytesseract after configuration

# -----------------------------------------------------
# Configuration Function
# -----------------------------------------------------

def configure_utils(config_data, tesseract_cmd, tesseract_ok):
    """
    Initializes configuration settings, sets the Tesseract command path 
    for pytesseract, and updates the Tesseract status.
    """
    global CONFIG, TESSERACT_OK
    
    CONFIG.update(config_data)
    TESSERACT_OK = tesseract_ok
    
    # FIX: Crucially, set the global tesseract_cmd path for the pytesseract library itself
    try:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    except AttributeError:
        # Handle cases where pytesseract is not imported/installed correctly
        logger.error("Failed to set pytesseract.tesseract_cmd. Check pytesseract version/installation.")
        TESSERACT_OK = False
        
    logger.info("Utils configured successfully.")

# -----------------------------------------------------
# Utility Functions
# -----------------------------------------------------

def allowed_file(filename):
    """Checks if a file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in CONFIG.get('ALLOWED_EXTENSIONS', set())

def delete_file(filename):
    """Deletes a file from the upload folder."""
    # Ensure a basic check is done on the filename to prevent accidental folder path use
    if not filename or os.path.sep in filename or os.path.altsep in filename:
        logger.warning(f"Attempt to delete invalid filename: {filename}")
        return False

    filepath = os.path.join(CONFIG.get('UPLOAD_FOLDER', 'uploads'), filename)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.debug(f"Successfully deleted file: {filename}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error deleting file {filename}: {e}")
        return False

def cleanup_old_files():
    """Removes files older than CLEANUP_AGE_SECONDS."""
    upload_folder = CONFIG.get('UPLOAD_FOLDER', 'uploads')
    cleanup_age = CONFIG.get('CLEANUP_AGE_SECONDS', 3600)
    
    if not os.path.isdir(upload_folder):
        return

    now = datetime.datetime.now()
    
    for filename in os.listdir(upload_folder):
        filepath = os.path.join(upload_folder, filename)
        
        if os.path.isfile(filepath):
            try:
                # FIX: Add try-except block around os.path.getmtime
                file_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(filepath))
            except OSError as e:
                logger.warning(f"Skipping file {filename}: Could not get modification time. Error: {e}")
                continue
                
            file_age_seconds = (now - file_mtime).total_seconds()
            
            if file_age_seconds > cleanup_age:
                # delete_file handles its own logging
                delete_file(filename)


# -----------------------------------------------------
# OCR Core Function 
# -----------------------------------------------------

def perform_ocr(filepath, lang='eng', psm='3'):
    """Performs OCR using pytesseract and handles errors."""
    
    if not TESSERACT_OK:
        # FIX: Provide a more informative error message
        logger.error("Attempted OCR but Tesseract status is not OK. Check Tesseract path.")
        return {'status': 'error', 'message': 'Tesseract not configured or found. Check your TESSERACT_CMD path.'}

    tess_config = f'--psm {psm}'
    
    try:
        # Use pytesseract.image_to_string, which now relies on the globally set tesseract_cmd
        ocr_text = pytesseract.image_to_string(
            filepath,
            lang=lang, 
            config=tess_config,
            timeout=60 # Set a timeout (e.g., 60 seconds) to prevent infinite hang
        )

        if not ocr_text.strip():
            logger.warning(f'OCR extracted no text for {filepath} (Lang: {lang})')
            return {'status': 'error', 'message': f'OCR completed but extracted no text. Check language pack ("{lang}") or image quality.'}

        return {'status': 'success', 'text': ocr_text}

    except pytesseract.TesseractNotFoundError:
        # This error is less likely if TESSERACT_OK check passed, but good to keep
        logger.error("Tesseract not found during execution.")
        return {'status': 'error', 'message': 'Tesseract not found. Check installation and TESSERACT_CMD path.'}
    
    except pytesseract.TesseractError as e:
        logger.error(f"Tesseract execution failed (Lang: {lang}): {e}")
        return {'status': 'error', 'message': f'OCR failed during execution. Cause: {e}. Check Tesseract language data for "{lang}".'}

    except TimeoutError:
        logger.error(f"Tesseract process timed out for file: {filepath}")
        return {'status': 'error', 'message': 'OCR process timed out (exceeded 60 seconds). Try a smaller file or simpler image.'}
        
    except Exception as e:
        logger.error(f"An unexpected error occurred during OCR: {e}", exc_info=True)
        return {'status': 'error', 'message': f'An unexpected server error occurred: {type(e).__name__}'}

