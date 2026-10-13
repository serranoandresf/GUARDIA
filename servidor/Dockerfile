FROM python:3.12-slim
RUN useradd -m guardia && mkdir /datos && chown guardia /datos
WORKDIR /app
COPY servidor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY servidor/*.py .
COPY web ./web
USER guardia
ENV GUARDIA_DATOS=/datos GUARDIA_WEB=/app/web
CMD ["uvicorn", "servidor:app", "--host", "0.0.0.0", "--port", "8000"]
