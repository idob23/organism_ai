FROM python:3.11-slim

# MEDIA-1: ffmpeg for video frame extraction
# SKILL-1: Node.js for quality .docx files
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg nodejs npm && \
    rm -rf /var/lib/apt/lists/*

# Bazovye pakety dlja vychislenij + vizualizacii + PDF
RUN pip install --no-cache-dir \
    numpy==1.26.4 \
    pandas==2.2.1 \
    openpyxl \
    reportlab \
    pypdf2 \
    matplotlib \
    fpdf2

# Rabochaja direktorija
WORKDIR /sandbox

# SKILL-2: DejaVu fonts for Cyrillic PDF via fpdf2
COPY fonts/ /sandbox/fonts/

# SKILL-1: docx library installed locally in /sandbox
RUN npm init -y && npm install --fetch-timeout=120000 docx && \
    node -e "require('docx'); console.log('docx OK')"

# Neprivilegirovannyj polzovatel
RUN useradd -m -u 1000 sandbox && \
    mkdir -p /output && chown sandbox:sandbox /output && \
    chown sandbox:sandbox /sandbox
USER sandbox

# Tochka vkhoda — chitaem kod iz stdin
CMD ["python", "-"]
