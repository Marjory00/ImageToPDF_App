# config.py

import os

class Config:
    """Base configuration settings."""
    # General App Settings
    FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'default_fallback_secret_for_local_testing_only')
    
    # File Management
    UPLOAD_FOLDER = 'uploads'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'tif', 'tiff', 'pdf'}
    MAX_FILE_SIZE = 5 * 1024 * 1024 # 5 MB
    CLEANUP_AGE_SECONDS = 3600 # 1 hour
    MAX_FILENAME_LENGTH = 128
    
    # OCR Engine
    # Store the environment variable directly, without the fallback, so we can check it later
    TESSERACT_CMD = os.environ.get('TESSERACT_CMD') or 'tesseract'

    # Rate Limiting 
    LIMITER_OCR_ROUTE_LIMITS = ("5 per minute", "30 per hour")
    LIMITER_DEFAULT_LIMITS = ("200 per day", "50 per hour")
    
    # Explicitly set the storage URI to remove the UserWarning.
    LIMITER_STORAGE_URI = os.environ.get('LIMITER_STORAGE_URI', 'memory://')
    
    # Logging
    LOG_LEVEL = 'INFO'
    
class DevelopmentConfig(Config):
    """Configuration for the development environment."""
    DEBUG = True
    LOG_LEVEL = 'DEBUG'

class TestingConfig(Config):
    """Configuration for running tests."""
    TESTING = True
    DEBUG = True
    # Use a separate folder for test uploads to avoid conflicts
    UPLOAD_FOLDER = 'test_uploads' 
    # Use a faster cleanup time for testing
    CLEANUP_AGE_SECONDS = 5 

class ProductionConfig(Config):
    """Configuration for the production environment."""
    DEBUG = False
    LOG_LEVEL = 'WARNING'
    
    # NOTE: In a real production setup, LIMITER_STORAGE_URI should be overridden 
    # here to point to Redis or Memcached.
    # e.g., LIMITER_STORAGE_URI = os.environ.get('LIMITER_STORAGE_URI', 'redis://localhost:6379/0')


# Mapping to load the correct config based on an environment variable
def get_config(env_name=None):
    """Get configuration object based on environment name."""
    env = env_name or os.environ.get('FLASK_ENV', 'development').lower()
    
    if env == 'testing':
        return TestingConfig()
    elif env == 'production':
        # FIX: Check if the TESSERACT_CMD was NOT explicitly set by the user 
        # (i.e., it fell back to 'tesseract'), and if so, raise an error.
        # This forces the user to provide a path in production.
        if Config.TESSERACT_CMD == 'tesseract' and not os.environ.get('TESSERACT_CMD'):
            raise Exception('TESSERACT_CMD environment variable must be set to the absolute path of tesseract in production.')

        # The secret key check now runs only when production config is loaded.
        if Config.FLASK_SECRET_KEY == 'default_fallback_secret_for_local_testing_only':
            raise Exception('FLASK_SECRET_KEY must be set in the production environment.')
            
        return ProductionConfig()
    else:
        return DevelopmentConfig()