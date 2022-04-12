FROM {{ service_base_image.image }}

COPY MANIFEST.in setup.py /website/
COPY docs.tar.gz /
RUN --mount=src=website,target=/website/website \
    tar -xvzf /docs.tar.gz --no-same-owner && \
    hail-pip-install /website && \
    chmod -R 0444 $(python3 -m pip show website | grep -E '^Location: ' | sed 's/Location: //')

CMD ["python3", "-m", "website"]
