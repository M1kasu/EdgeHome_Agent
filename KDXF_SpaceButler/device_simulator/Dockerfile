FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY spacebutler_simulator ./spacebutler_simulator

CMD ["python", "-m", "spacebutler_simulator.app"]
