FROM python:3.12-slim

# tini for proper PID 1 signal handling (needed for pty/terminal)
RUN apt-get update && apt-get install -y --no-install-recommends tini && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DOCKER_CONTAINER=1
ENV PORT=5111

EXPOSE 5111

ENTRYPOINT ["tini", "--"]
CMD ["python", "server.py", "--remote", "--no-browser"]
