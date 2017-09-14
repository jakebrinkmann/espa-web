FROM python:2.7

RUN mkdir -p /home/espadev/espa-web
WORKDIR /home/espadev/espa-web

COPY setup/requirements.txt /home/espadev/espa-web
RUN pip install --no-cache-dir -r requirements.txt  --index-url=http://pypi.python.org/simple/ --trusted-host pypi.python.org

ENV ESPAWEB_SETTINGS=/home/espadev/.usgs/espaweb-settings.ini
ENV ESPA_ENV=dev
ENV ESPA_WEB_EMAIL_RECEIVE="someone@somewhere.com"
ENV ESPA_API_HOST="http://localhost:4004/api/v1"

COPY . /home/espadev/espa-web
RUN mkdir -p /home/espadev/.usgs && \
	ln -s /home/espadev/espa-web/run/config.ini /home/espadev/.usgs/espaweb-settings.ini

EXPOSE 4001
ENTRYPOINT ["uwsgi", "run/espadev-uwsgi.ini"]
