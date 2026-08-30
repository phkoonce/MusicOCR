"""MusicOCR — automated PDF -> MusicXML / MuseScore pipeline built on Audiveris."""
import warnings

__version__ = "0.1.0"

# music21 pulls in requests -> urllib3, which warns loudly about the system
# LibreSSL. Nothing here makes HTTPS calls, so silence it.
warnings.filterwarnings("ignore", message=r".*OpenSSL 1\.1\.1\+.*")
