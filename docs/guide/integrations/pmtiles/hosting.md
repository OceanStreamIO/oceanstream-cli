# PMTiles Hosting Guide

Complete guide to hosting PMTiles files for production use.

## Overview

PMTiles files use HTTP range requests, enabling efficient tile delivery without a dedicated tile server. This makes hosting simple and cost-effective.

**Key Requirements**:
- HTTP range request support
- Public read access (or authenticated access)
- CORS headers (for cross-domain access)
- Optional: CDN for global distribution

## Azure Blob Storage (Recommended)

### 1. Upload PMTiles to Azure

```bash
# Install Azure CLI
brew install azure-cli  # macOS
# or apt-get install azure-cli  # Linux

# Login
az login

# Upload PMTiles
az storage blob upload \
  --account-name <storage-account> \
  --container-name tiles \
  --name track.pmtiles \
  --file ./output/tiles/track.pmtiles \
  --content-type application/vnd.pmtiles
```

### 2. Configure Public Access

```bash
# Enable public read access for blob
az storage blob set-tier \
  --account-name <storage-account> \
  --container-name tiles \
  --name track.pmtiles \
  --tier Hot

# Set public access level
az storage container set-permission \
  --account-name <storage-account> \
  --name tiles \
  --public-access blob
```

### 3. Configure CORS

Create `cors.json`:
```json
[
  {
    "allowedOrigins": ["*"],
    "allowedMethods": ["GET", "HEAD"],
    "allowedHeaders": ["*"],
    "exposedHeaders": ["*"],
    "maxAgeInSeconds": 3600
  }
]
```

Apply CORS:
```bash
az storage cors add \
  --account-name <storage-account> \
  --services b \
  --methods GET HEAD \
  --origins "*" \
  --allowed-headers "*" \
  --exposed-headers "*" \
  --max-age 3600
```

### 4. Use in MapLibre

```javascript
map.addSource('oceanstream', {
  type: 'vector',
  url: 'pmtiles://https://<storage-account>.blob.core.windows.net/tiles/track.pmtiles'
});
```

### 5. Enable CDN (Optional)

```bash
# Create CDN profile
az cdn profile create \
  --name oceanstream-cdn \
  --resource-group <resource-group> \
  --sku Standard_Microsoft

# Create CDN endpoint
az cdn endpoint create \
  --name oceanstream-tiles \
  --profile-name oceanstream-cdn \
  --resource-group <resource-group> \
  --origin <storage-account>.blob.core.windows.net \
  --origin-host-header <storage-account>.blob.core.windows.net
```

**CDN URL**:
```javascript
url: 'pmtiles://https://oceanstream-tiles.azureedge.net/tiles/track.pmtiles'
```

## AWS S3

### 1. Upload to S3

```bash
# Install AWS CLI
brew install awscli  # macOS

# Configure credentials
aws configure

# Upload PMTiles
aws s3 cp ./output/tiles/track.pmtiles \
  s3://oceanstream-tiles/track.pmtiles \
  --content-type application/vnd.pmtiles
```

### 2. Configure Public Access

```bash
# Set public-read ACL
aws s3api put-object-acl \
  --bucket oceanstream-tiles \
  --key track.pmtiles \
  --acl public-read
```

### 3. Configure CORS

Create `cors.json`:
```json
{
  "CORSRules": [
    {
      "AllowedOrigins": ["*"],
      "AllowedMethods": ["GET", "HEAD"],
      "AllowedHeaders": ["*"],
      "ExposeHeaders": ["ETag"],
      "MaxAgeSeconds": 3600
    }
  ]
}
```

Apply CORS:
```bash
aws s3api put-bucket-cors \
  --bucket oceanstream-tiles \
  --cors-configuration file://cors.json
```

### 4. Use in MapLibre

```javascript
map.addSource('oceanstream', {
  type: 'vector',
  url: 'pmtiles://https://oceanstream-tiles.s3.amazonaws.com/track.pmtiles'
});
```

### 5. Enable CloudFront (Optional)

```bash
# Create CloudFront distribution
aws cloudfront create-distribution \
  --origin-domain-name oceanstream-tiles.s3.amazonaws.com \
  --default-root-object index.html
```

**CloudFront URL**:
```javascript
url: 'pmtiles://https://d1234567890.cloudfront.net/track.pmtiles'
```

## Local Development

### Python HTTP Server

```bash
cd output/tiles
python -m http.server 8000

# Access: http://localhost:8000/track.pmtiles
```

### Node.js HTTP Server

```bash
npx http-server ./output/tiles -p 8000 --cors

# Access: http://localhost:8000/track.pmtiles
```

### Nginx

Create `nginx.conf`:
```nginx
server {
  listen 8000;
  server_name localhost;
  
  location /tiles/ {
    alias /path/to/output/tiles/;
    add_header Access-Control-Allow-Origin *;
    add_header Access-Control-Allow-Methods 'GET, HEAD';
  }
}
```

Start Nginx:
```bash
nginx -c nginx.conf
```

## Static Site Hosting

### GitHub Pages

1. Create repository: `oceanstream-viewer`
2. Add files:
   ```
   docs/
   ├── index.html      # Map viewer
   └── tiles/
       └── track.pmtiles
   ```
3. Enable GitHub Pages in Settings → Pages
4. Use URL:
   ```javascript
   url: 'pmtiles://https://<username>.github.io/oceanstream-viewer/tiles/track.pmtiles'
   ```

### Netlify

1. Create `netlify.toml`:
   ```toml
   [[headers]]
     for = "/tiles/*"
     [headers.values]
       Access-Control-Allow-Origin = "*"
       Access-Control-Allow-Methods = "GET, HEAD"
   ```
2. Deploy:
   ```bash
   netlify deploy --prod --dir=./output
   ```
3. Use URL:
   ```javascript
   url: 'pmtiles://https://your-site.netlify.app/tiles/track.pmtiles'
   ```

### Vercel

1. Create `vercel.json`:
   ```json
   {
     "headers": [
       {
         "source": "/tiles/(.*)",
         "headers": [
           { "key": "Access-Control-Allow-Origin", "value": "*" },
           { "key": "Access-Control-Allow-Methods", "value": "GET, HEAD" }
         ]
       }
     ]
   }
   ```
2. Deploy:
   ```bash
   vercel --prod
   ```
3. Use URL:
   ```javascript
   url: 'pmtiles://https://your-site.vercel.app/tiles/track.pmtiles'
   ```

## CORS Configuration

### Required Headers

```
Access-Control-Allow-Origin: *
Access-Control-Allow-Methods: GET, HEAD
Access-Control-Allow-Headers: Range
Access-Control-Expose-Headers: Content-Length, Content-Range
```

### Apache (.htaccess)

```apache
<IfModule mod_headers.c>
  <FilesMatch "\.(pmtiles)$">
    Header set Access-Control-Allow-Origin "*"
    Header set Access-Control-Allow-Methods "GET, HEAD"
    Header set Access-Control-Allow-Headers "Range"
    Header set Access-Control-Expose-Headers "Content-Length, Content-Range"
  </FilesMatch>
</IfModule>
```

### Nginx

```nginx
location /tiles/ {
  add_header Access-Control-Allow-Origin *;
  add_header Access-Control-Allow-Methods 'GET, HEAD';
  add_header Access-Control-Allow-Headers 'Range';
  add_header Access-Control-Expose-Headers 'Content-Length, Content-Range';
}
```

## Cost Optimization

### Azure Blob Storage

**Storage costs** (per month):
- Hot tier: ~$0.018/GB
- Cool tier: ~$0.01/GB (use for infrequently accessed tiles)

**Bandwidth costs**:
- First 100 GB/month: Free
- 100 GB - 10 TB: $0.087/GB

**Example**:
- 10 MB PMTiles file
- 1,000 users/month, 5 MB downloaded per user
- **Total**: ~$0.44/month

### AWS S3

**Storage costs** (per month):
- Standard: ~$0.023/GB
- Infrequent Access: ~$0.0125/GB

**Bandwidth costs**:
- First 100 GB/month: Free
- 100 GB - 10 TB: $0.09/GB

**Example**:
- 10 MB PMTiles file
- 1,000 users/month, 5 MB downloaded per user
- **Total**: ~$0.45/month

### CDN Benefits

Using a CDN reduces costs:
- Cache tiles at edge locations
- Reduce origin bandwidth (only first request hits origin)
- Faster load times globally

**Example** (Azure CDN):
- First 100 GB/month: Free
- 100 GB - 10 TB: $0.081/GB
- **Savings**: ~15-20% vs direct storage access

## Security

### Authenticated Access

**Azure Blob Storage with SAS tokens**:
```bash
# Generate SAS token (1-hour expiry)
az storage blob generate-sas \
  --account-name <storage-account> \
  --container-name tiles \
  --name track.pmtiles \
  --permissions r \
  --expiry $(date -u -d '1 hour' '+%Y-%m-%dT%H:%MZ') \
  --https-only \
  --output tsv
```

**Use in MapLibre**:
```javascript
const sasToken = 'se=2024-12-03T00%3A00%3A00Z&sp=r&sv=2021-06-08&sr=b&sig=...';
map.addSource('oceanstream', {
  type: 'vector',
  url: `pmtiles://https://<storage-account>.blob.core.windows.net/tiles/track.pmtiles?${sasToken}`
});
```

**S3 with Pre-signed URLs**:
```bash
# Generate pre-signed URL (1-hour expiry)
aws s3 presign s3://oceanstream-tiles/track.pmtiles \
  --expires-in 3600
```

### IP Restrictions

**Azure Blob Storage**:
```bash
az storage account network-rule add \
  --account-name <storage-account> \
  --ip-address <your-ip>
```

**S3 Bucket Policy**:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::oceanstream-tiles/*",
      "Condition": {
        "IpAddress": {
          "aws:SourceIp": ["203.0.113.0/24"]
        }
      }
    }
  ]
}
```

## Monitoring

### Azure Monitor

```bash
# Enable blob storage diagnostics
az monitor diagnostic-settings create \
  --resource /subscriptions/<sub-id>/resourceGroups/<rg>/providers/Microsoft.Storage/storageAccounts/<account> \
  --name blob-diagnostics \
  --logs '[{"category":"StorageRead","enabled":true}]' \
  --metrics '[{"category":"Transaction","enabled":true}]' \
  --workspace <log-analytics-workspace-id>
```

**Key metrics**:
- Request count
- Bandwidth usage
- Average latency
- Error rate

### AWS CloudWatch

```bash
# Enable S3 request metrics
aws s3api put-bucket-metrics-configuration \
  --bucket oceanstream-tiles \
  --id EntireBucket \
  --metrics-configuration '{"Id":"EntireBucket"}'
```

**Key metrics**:
- AllRequests
- BytesDownloaded
- FirstByteLatency
- 4xxErrors

## Performance Best Practices

1. **Use CDN**: Cache tiles at edge locations
2. **Enable compression**: PMTiles already compressed, but enable gzip for HTTP
3. **Set cache headers**:
   ```
   Cache-Control: public, max-age=31536000, immutable
   ```
4. **Use HTTP/2**: Multiplexing improves range request performance
5. **Monitor bandwidth**: Set up alerts for unexpected traffic spikes

## Checklist

Before going to production:

- [ ] PMTiles uploaded to cloud storage
- [ ] Public access or SAS tokens configured
- [ ] CORS headers enabled
- [ ] CDN enabled (optional but recommended)
- [ ] Cache headers set
- [ ] Monitoring enabled
- [ ] Cost alerts configured
- [ ] Tested from multiple locations
- [ ] Verified range request support
- [ ] HTTPS enforced

## Next Steps

- [Web Integration](web-integration.md) - Build map viewer
- [Configuration](configuration.md) - Customize PMTiles generation
- [Troubleshooting](troubleshooting.md) - Common hosting issues
- [Overview](overview.md) - PMTiles concepts
