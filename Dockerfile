FROM python:3.10-slim

# O numpy/OpenBLAS abre 16 threads por thread que o chama. Com o pool de download
# do scan isso estourou a memoria do processo em producao; travar em 1 resolve.
ENV OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    VECLIB_MAXIMUM_THREADS=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y build-essential curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Diretorios de dados: ficam fora do git, entao precisam existir na imagem.
# Monte-os como volume na VPS para o historico sobreviver a um redeploy.
RUN mkdir -p extracted_images reports cache_extracao historico auditoria/documentos

EXPOSE 5000

# Um unico worker de proposito: o progresso do scan, o cache de pares e o
# historico em andamento vivem na memoria do processo. Com 2+ workers cada
# requisicao cairia num processo diferente e o painel perderia o estado.
# As threads cobrem as requisicoes simultaneas do painel.
#
# timeout alto porque a varredura de uma fase e o OCR rodam dentro do processo
# por dezenas de minutos: com o padrao de 30 s o arbiter mataria o worker no
# meio do trabalho e o scan seria perdido.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "1800", \
     "--graceful-timeout", "60", "--access-logfile", "-", \
     "--bind", "0.0.0.0:5000", "app:app"]
