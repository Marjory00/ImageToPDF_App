
# security.py

import os
import re
from werkzeug.utils import secure_filename
import logging

logger = logging.getLogger(__name__)

# --- CONFIGURATION (Set via app.py for consistency) ---

UPLOAD_FOLDER = 'uploads'
MAX_FILENAME_LENGTH = 128

def configure_security(upload_folder):
    """Initializes global variables within security.py from the main app config."""
    global UPLOAD_FOLDER
    UPLOAD_FOLDER = upload_folder

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
    if not filename:
        logger.warning("Filename validation failed: empty filename.")
        return None

    # 1. Truncate long filenames before securing
    name_part, ext_part = os.path.splitext(filename)
    
    # Calculate available space for the name part
    max_name_len = MAX_FILENAME_LENGTH - len(ext_part) - 1 # -1 for the dot
    
    if unique_id:
        max_name_len -= (len(unique_id) + 1) # +1 for the underscore separator
        
    if len(name_part) > max_name_len:
        name_part = name_part[:max_name_len]
        logger.info(f"Filename truncated from {len(filename)} to {MAX_FILENAME_LENGTH}.")

    # Reassemble the truncated filename
    truncated_filename = name_part + ext_part

    # 2. Use Werkzeug's secure_filename to prevent directory traversal (e.g., "../")
    secured_filename = secure_filename(truncated_filename)

    # 3. Final sanitization (e.g., removing any leading/trailing dots or spaces)
    secured_filename = secured_filename.strip(' ._')
    
    if not secured_filename:
        logger.error(f"Filename '{filename}' resulted in an empty string after security checks.")
        return None

    # 4. Apply unique prefix
    if unique_id:
        final_filename = f"{unique_id}_{secured_filename}"
    else:
        final_filename = secured_filename

    # 5. Final check against reserved names (optional but good practice)
    if final_filename.lower() in ('con', 'prn', 'aux', 'nul', 'com1', 'lpt1'):
        logger.error(f"Filename '{final_filename}' is a reserved system name.")
        return None

    # 6. Ensure the full path isn't excessive (though we control the folder)
    if len(final_filename) > MAX_FILENAME_LENGTH + 17: # Allowing buffer for unique_id + underscore
         logger.error(f"Final filename length exceeds safety limit: {final_filename}")
         return None
         
    return final_filename

def get_unique_filename(original_filename):
    """Generates a secure, unique filename for saving the uploaded file."""
    # Generate a strong, random task ID
    task_id = os.urandom(16).hex()
    
    # Validate and secure the filename using the task_id as the unique prefix
    safe_filename = validate_and_secure_filename(original_filename, unique_id=task_id)

    if safe_filename:
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