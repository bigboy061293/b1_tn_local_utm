#!/bin/sh
# Sinh CA hành lang, chứng chỉ máy chủ cổng vào và chứng chỉ khách cho bản sao số (chỉ dùng khi phát triển).
set -e
D=${1:-.dev/ingress}
mkdir -p "$D" && cd "$D"
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout ca.key -out ca.pem -days 30 -subj "/CN=B1 Corridor Dev CA" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout server.key -out server.csr -subj "/CN=localhost" 2>/dev/null
printf "subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\nkeyUsage=digitalSignature\n" > san.ext
printf "extendedKeyUsage=clientAuth\nkeyUsage=digitalSignature\n" > client.ext
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out server.pem -days 30 -extfile san.ext 2>/dev/null
openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -keyout client.key -out client.csr -subj "/CN=kh-at4/O=Khach AT4" 2>/dev/null
openssl x509 -req -in client.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out client.pem -days 30 -extfile client.ext 2>/dev/null
echo "$D"
