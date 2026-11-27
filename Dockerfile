FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY b1 ./b1
RUN useradd -r b1 && chown -R b1 /app
USER b1
EXPOSE 8000
CMD ["uvicorn", "--factory", "b1.app:create_app", "--host", "0.0.0.0", "--port", "8000"]
