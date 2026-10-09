FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libasound2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY compat_preload.py ./
RUN python compat_preload.py
COPY agent.py bilingual_stt.py mimo_tts.py token_server.py llm_options.py azure_streaming_tts.py ./
COPY tests ./tests
RUN python -m unittest discover -s tests -v
RUN DEEPGRAM_API_KEY=fictional LLM_API_KEY=fictional LLM_BASE_URL=https://fictional.invalid AZURE_SPEECH_KEY=fictional AZURE_SPEECH_REGION=koreacentral python -c "import agent; assert agent.server; assert agent.Assistant().instructions == ''"
RUN python agent.py download-files
RUN python -c "import livekit.agents.ipc._preload; from livekit.plugins import silero; assert silero.VAD.load()"
RUN python -c "from azure_streaming_tts import AzureStreamingTTS; t=AzureStreamingTTS(speech_key='fictional', speech_region='koreacentral'); assert t.capabilities.streaming; t._synthesizer.synthesizing.disconnect_all()"
CMD ["python", "agent.py", "start"]
