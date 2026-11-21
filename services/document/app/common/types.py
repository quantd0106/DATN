from typing import Literal

DocumentFormat = Literal["PDF", "PPTX", "DOCX", "TXT", "MARKDOWN"]
ProcessingStatus = Literal["PENDING", "PROCESSING", "SUCCESS", "NEEDS_REVIEW", "FAILED"]
AcceptanceMode = Literal["AUTO_PUBLISHED", "OWNER_ACCEPTED"]
