from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

ALLOWED_EXTENSIONS = ["pdf", "jpg", "jpeg", "png"]
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

validate_extension = FileExtensionValidator(ALLOWED_EXTENSIONS)


def validate_file_size(file):
    if file.size > MAX_UPLOAD_BYTES:
        raise ValidationError("The file is larger than 10 MB. Please upload a smaller scan.")
