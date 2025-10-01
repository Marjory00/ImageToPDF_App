
# test_ocr.py

import unittest
import os
import shutil
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import logging

# Ensure the parent directory is in the path to import sibling modules
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))

import utils
import security
from config import get_config, TestingConfig

# Set up logging for tests
logging.basicConfig(level=logging.INFO)

class TestOCRAppCoreLogic(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Set up the testing environment and configuration."""
        cls.config = get_config('testing')
        cls.TEST_UPLOAD_FOLDER = cls.config.UPLOAD_FOLDER
        
        # Configure utils and security with test settings
        utils.configure_utils({
            'ALLOWED_EXTENSIONS': cls.config.ALLOWED_EXTENSIONS,
            'UPLOAD_FOLDER': cls.TEST_UPLOAD_FOLDER,
            'CLEANUP_AGE_SECONDS': cls.config.CLEANUP_AGE_SECONDS,
            'MAX_FILE_SIZE': cls.config.MAX_FILE_SIZE
        }, cls.config.TESSERACT_CMD, False) # Force Tesseract to NOT be OK for initial tests
        
        security.configure_security(cls.TEST_UPLOAD_FOLDER)

        # Create the test upload directory
        os.makedirs(cls.TEST_UPLOAD_FOLDER, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        """Clean up the testing environment."""
        if os.path.exists(cls.TEST_UPLOAD_FOLDER):
            shutil.rmtree(cls.TEST_UPLOAD_FOLDER)
            
# --- UTILS TESTS ---

    def test_01_allowed_file(self):
        """Test file extension validation."""
        self.assertTrue(utils.allowed_file("test.png"))
        self.assertTrue(utils.allowed_file("document.pdf"))
        self.assertFalse(utils.allowed_file("script.exe"))
        self.assertFalse(utils.allowed_file("archive.zip"))

    def test_02_cleanup_old_files(self):
        """Test the cleanup logic for deleting old files."""
        # Create a fresh folder for cleanup test
        test_folder = os.path.join(self.TEST_UPLOAD_FOLDER, 'cleanup_temp')
        os.makedirs(test_folder, exist_ok=True)
        
        # Create a file that is too old (older than CLEANUP_AGE_SECONDS)
        old_file_path = os.path.join(test_folder, 'old.txt')
        with open(old_file_path, 'w') as f:
            f.write("old data")
        
        # Set modification time to be past the cleanup age (e.g., 2 hours ago)
        old_timestamp = datetime.now() - timedelta(seconds=self.config.CLEANUP_AGE_SECONDS + 10)
        os.utime(old_file_path, (old_timestamp.timestamp(), old_timestamp.timestamp()))

        # Create a fresh file (should not be deleted)
        new_file_path = os.path.join(test_folder, 'new.txt')
        with open(new_file_path, 'w') as f:
            f.write("new data")
        
        # Patch the UPLOAD_FOLDER for this test to target the temp folder
        with patch.object(utils, 'UPLOAD_FOLDER', test_folder):
            utils.cleanup_old_files()

        # Assertions
        self.assertFalse(os.path.exists(old_file_path), "Old file was not deleted.")
        self.assertTrue(os.path.exists(new_file_path), "New file was prematurely deleted.")
        
        # Clean up the temp folder
        shutil.rmtree(test_folder)

    @patch('utils.pytesseract.pytesseract.tesseract_cmd', new_callable=MagicMock)
    @patch.object(utils, 'TESSERACT_OK', False)
    def test_03_perform_ocr_tesseract_not_ok(self, mock_cmd):
        """Test OCR failure when Tesseract is not configured."""
        result = utils.perform_ocr('/fake/path/img.png', 'eng', '3')
        self.assertEqual(result['status'], 'error')
        self.assertIn('not installed correctly', result['message'])

# --- SECURITY TESTS ---

    def test_04_validate_and_secure_filename(self):
        """Test filename sanitization and security."""
        # Test basic security (directory traversal)
        unsafe_name = "../etc/passwd"
        safe_name = security.validate_and_secure_filename(unsafe_name)
        self.assertNotIn('..', safe_name)
        self.assertNotIn('/', safe_name)

        # Test with unique ID prefix
        original_name = "My Document!.pdf"
        task_id = "a1b2c3d4e5f6g7h8"
        secured = security.validate_and_secure_filename(original_name, task_id)
        self.assertTrue(secured.startswith(task_id + '_'))
        self.assertIn('MyDocument.pdf', secured) # secure_filename removes '!'

        # Test reserved names
        self.assertIsNone(security.validate_and_secure_filename("CON.txt"))

    def test_05_is_safe_to_serve(self):
        """Test security check against path escape (LFI protection)."""
        # Safe name test
        self.assertTrue(security.is_safe_to_serve("a1b2c3d4e5f6g7h8_my_file.png"))
        
        # Unsafe name test (path traversal attempt)
        self.assertFalse(security.is_safe_to_serve("../../../etc/passwd"))
        self.assertFalse(security.is_safe_to_serve("..\\..\\config.ini")) # Windows traversal

# --- CONFIG TESTS ---

    def test_06_config_loading(self):
        """Test configuration object properties based on environment."""
        dev_config = get_config('development')
        self.assertTrue(dev_config.DEBUG)
        self.assertEqual(dev_config.LOG_LEVEL, 'DEBUG')
        
        test_config = get_config('testing')
        self.assertTrue(test_config.TESTING)
        self.assertEqual(test_config.UPLOAD_FOLDER, 'test_uploads')
        
        # Test production config requires a proper secret key (mock the OS env)
        with self.assertRaises(Exception):
            with patch.dict(os.environ, {'FLASK_ENV': 'production', 'FLASK_SECRET_KEY': TestingConfig.FLASK_SECRET_KEY}):
                # Mock the base config fallback to trigger the exception
                with patch.object(TestingConfig, 'FLASK_SECRET_KEY', 'default_fallback_secret_for_local_testing_only'):
                     get_config('production') 

# --- EXECUTION ---

if __name__ == '__main__':
    # To run this file, execute: python test_ocr.py
    unittest.main()