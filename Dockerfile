FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY TempVCBot.py healthcheck.py room_state.py room_service.py control_messages.py test_deployment.py ./
COPY tests/ ./tests/
USER 10001:10001
CMD ["python", "TempVCBot.py"]
