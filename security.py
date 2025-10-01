# security.py

import os
# import re # FIX: Removed unused 're' import
from werkzeug.utils import secure_filename
import logging
# FIX: Added line break for clarity and fixed merged import statement

logger = logging.getLogger(__name__)

# --- GLOBAL CONFIGURATION (Will be updated by configure_security) ---

# Set to default values that should be overridden by app.py/config.py
UPLOAD_FOLDER = 'uploads'
# Max length for the SECURED name *excluding* the unique ID and separator
MAX_FILENAME_LENGTH = 128 

def configure_security(upload_folder, max_filename_length):
    """Initializes global variables within security.py from the main app config."""
    global UPLOAD_FOLDER, MAX_FILENAME_LENGTH
    UPLOAD_FOLDER = upload_folder
    MAX_FILENAME_LENGTH = max_filename_length
    logger.info(f"Security utilities configured. Upload folder: {UPLOAD_FOLDER}")

# --- FILENAME SECURITY UTILITIES ---

def validate_and_secure_filename(filename, unique_id=None):
    """
    Validates a filename for length and ensures it is safe against directory traversal
    and injection using secure_filename, optionally prefixing a unique ID.
    
    Args:
        filename (str): The original filename provided by the user.
        unique_id (str, optional): A unique prefix (e.g., UUID or task_id).

    Returns:
        str: A safe, truncated, and possibly prefixed filename, or None if invalid.
    """
    if not filename or not filename.strip():
        logger.warning("Filename validation failed: empty or whitespace-only filename.")
        return None

    # 1. Separate name and extension
    name_part, ext_part = os.path.splitext(filename)
    
    # 2. Secure the original filename first (handles traversal attempts early)
    secured_name_part = secure_filename(name_part)
    # NOTE: secured_ext_part will include the leading dot if one exists.
    secured_ext_part = secure_filename(ext_part) 
    
    # 3. Check for secure_filename resulting in empty or invalid name
    if not secured_name_part and secured_ext_part in ('.', ''):
         logger.error(f"Filename '{filename}' resulted in an empty base name after security checks.")
         return None

    # 4. Calculate max length for the secured base name (without unique_id/separator/ext)
    max_base_len = MAX_FILENAME_LENGTH

    # Account for the length added by the prefix (unique_id + underscore + extension)
    added_len = 0
    if unique_id:
        added_len += len(unique_id) + 1 # +1 for the underscore
    added_len += len(secured_ext_part)

    # Calculate max length available for the secured base name
    max_base_len -= added_len
    
    # Ensure max_base_len is at least 1, otherwise truncation makes no sense
    if max_base_len < 1:
        # If the prefix/extension are too long, allow one character for the base name
        logger.warning("Unique ID/Extension combination is too long for base filename limit. Allowing min length.")
        max_base_len = 1
        
    # 5. Truncate the secured base name
    if len(secured_name_part) > max_base_len:
        secured_name_part = secured_name_part[:max_base_len]
        logger.info(f"Filename base part truncated to {max_base_len} characters.")

    # 6. Reassemble the truncated, secured filename
    secured_filename = secured_name_part + secured_ext_part

    # 7. Apply unique prefix 
    if unique_id:
        final_filename = f"{unique_id}_{secured_filename}"
    else:
        final_filename = secured_filename

    # 8. Final check against reserved names (Windows compatibility)
    # Check against lowercased base name, excluding extension for safety
    base_name_no_ext = os.path.splitext(final_filename)[0].lower()
    if base_name_no_ext in ('con', 'prn', 'aux', 'nul', 'com1', 'lpt1', 'lpt2'):
        logger.error(f"Filename '{final_filename}' is a reserved system name.")
        return None
        
    return final_filename

def get_unique_filename(original_filename):
    """Generates a secure, unique filename for saving the uploaded file."""
    # Generate a strong, random task ID (32 chars)
    task_id = os.urandom(16).hex()
    
    # Validate and secure the filename using the task_id as the unique prefix
    safe_filename = validate_and_secure_filename(original_filename, unique_id=task_id)

    if safe_filename:
        # Check if the generated filename length is excessive (including the prefix)
        # 32 for task_id + 1 for underscore. This check is more for sanity.
        if len(safe_filename) > MAX_FILENAME_LENGTH + 33: 
             logger.error(f"Final generated filename length is excessive: {len(safe_filename)}")
             return None, None
        return task_id, safe_filename
    else:
        return None, None

def is_safe_to_serve(filename):
    """
    Checks if a file located in the uploads folder is safe to serve to a client.
    Primarily checks if the file path tries to escape the UPLOAD_FOLDER.
    """
    # Uses os.path.abspath to resolve any path manipulation attempts
    target_path = os.path.join(UPLOAD_FOLDER, filename)
    absolute_path = os.path.abspath(target_path)
    
    # Ensure the absolute path starts with the absolute path of the upload folder
    upload_abspath = os.path.abspath(UPLOAD_FOLDER)
    
    if not absolute_path.startswith(upload_abspath):
        logger.error(f"Security breach attempt: Path '{filename}' tried to escape '{UPLOAD_FOLDER}'.")
        return False
        
    return True