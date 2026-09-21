FROM guardian-p014-base:local

COPY entrypoint.sh /usr/local/bin/guardian-p014-workload
RUN chmod 0755 /usr/local/bin/guardian-p014-workload

HEALTHCHECK --interval=1s --timeout=1s --start-period=1s --retries=3 \
  CMD busybox wget -q -O - http://127.0.0.1:8080/health >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/guardian-p014-workload"]
