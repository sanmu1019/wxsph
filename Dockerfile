FROM python:3.13-slim

WORKDIR /app
COPY wxsph_api.py /app/wxsph_api.py

ENV WXSPH_HOST=0.0.0.0
ENV WXSPH_PORT=8787

EXPOSE 8787
CMD ["python", "/app/wxsph_api.py"]
