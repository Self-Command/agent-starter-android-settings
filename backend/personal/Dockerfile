FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py bilingual_stt.py mimo_tts.py token_server.py ./
COPY tests ./tests
RUN python -m unittest discover -s tests -v
RUN DEEPGRAM_API_KEY=fictional LLM_API_KEY=fictional LLM_BASE_URL=https://fictional.invalid MIMO_API_KEY=fictional python -c "import agent; assert agent.server"
RUN python agent.py download-files
CMD ["python", "agent.py", "start"]
