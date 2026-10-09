FROM python:3.12-slim
WORKDIR /srv/video_chat
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY server server
COPY app app
COPY download download
ENV SEEU_DEV=0 SEEU_UPLOAD_DIR=/data/uploads
EXPOSE 8000
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
