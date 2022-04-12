FROM {{ service_base_image.image }}

# Needed for the google-cloud-profiler
# Try to see if I can install less to get the profiler working
RUN hail-apt-get-install build-essential

COPY batch/requirements.txt batch/requirements.txt
RUN hail-pip-install -r batch/requirements.txt

COPY batch/setup.py batch/MANIFEST.in /batch/
RUN --mount=src=batch/batch,target=/batch/batch \
    hail-pip-install /batch

EXPOSE 5000
