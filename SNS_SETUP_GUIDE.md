# Amazon SNS Webhook Setup for Mail-Center

Your Sweet Shelves app now supports **real-time Amazon message notifications** via SNS (Simple Notification Service).

## Quick Summary

Instead of polling Amazon every few minutes, the system can now receive **instant notifications** when customers send messages. When a message arrives, an SNS event will be delivered to your app's webhook endpoint, and a notification will appear in mail-center.

## What Was Added

✓ **Webhook endpoint**: `/api/amazon-sns-webhook`  
✓ **SNS signature verification**: Ensures notifications are really from Amazon  
✓ **Mail storage**: Messages logged to mail-center database  

## Setup Steps

### 1. Create an AWS SNS Topic

1. Go to [AWS SNS Console](https://console.aws.amazon.com/sns/)
2. Click **Create topic**
3. Name it something like `amazon-seller-messages`
4. Choose **Standard** type
5. Click **Create topic**
6. Copy the **Topic ARN** (looks like: `arn:aws:sns:us-east-1:123456789:amazon-seller-messages`)

### 2. Create an SNS Subscription

1. In the topic you just created, click **Create subscription**
2. **Protocol**: HTTPS
3. **Endpoint**: `https://your-domain.com/api/amazon-sns-webhook`
   - Replace `your-domain.com` with your actual public domain
   - Example: `https://myapp.example.com/api/amazon-sns-webhook`
4. **Enable raw message delivery**: OFF (keep as is)
5. Click **Create subscription**
6. **Wait** - AWS will send a confirmation request to your endpoint
   - The app will auto-confirm this when SNS calls it
   - Check the subscription status in a few seconds (should show "Confirmed")

### 3. Register SNS Topic in Amazon Seller Central

1. Go to [Amazon Seller Central Developer Central](https://sellercentral.amazon.com/)
2. Navigate to **Apps and Services → Develop Apps**
3. Select your application
4. Go to **Subscriptions** or **Notifications**
5. Add a **new subscription**:
   - **Event Type**: `messaging:order:message` (or similar - look for "message received")
   - **Destination**: Paste your SNS Topic ARN from step 1
6. Click **Subscribe**

### 4. Test the Webhook

Send a test notification to verify it works:

```powershell
$payload = @{
    Type = "Notification"
    Message = '{"orderId":"111-1111111-1111111"}'
    Timestamp = "2026-02-07T12:00:00Z"
    TopicArn = "arn:aws:sns:us-east-1:123456789:your-topic"
    MessageId = "test-123"
    Subject = "Test"
    Signature = "dummy"
    SigningCertUrl = "https://sns.us-east-1.amazonaws.com/certs/dummy.pem"
} | ConvertTo-Json

Invoke-WebRequest `
    -Uri "https://your-domain.com/api/amazon-sns-webhook" `
    -Method POST `
    -ContentType "application/json" `
    -Body ($payload | ConvertTo-Json)
```

When a real customer message arrives on Amazon, it will automatically appear in your mail-center.

## How It Works

```
Customer Messages → Amazon Seller Central 
                    ↓
                Amazon SNS Topic
                    ↓
         Your App Webhook (/api/amazon-sns-webhook)
                    ↓
              Mail-Center Database
                    ↓
        Displays in your Mail-Center UI
```

## Signature Verification

The webhook automatically verifies that notifications come from Amazon by:
1. Checking the certificate URL is from `*.amazonaws.com`
2. Downloading Amazon's public certificate
3. Verifying the RSA signature on the message

This prevents spoofed notifications.

## What Appears in Mail-Center

When a message arrives, you'll see:

- **Store**: Amazon
- **From**: Amazon Customer
- **Subject**: 📨 Message on Amazon order [ORDER-ID]
- **Body**: Customer message notification with link to Seller Central
- **Status**: Unread

## Troubleshooting

### Subscription Shows "PendingConfirmation"
- Check your app logs for confirmation request
- The endpoint may be rejecting the confirmation
- Verify the URL is correct and publicly accessible

### Not Receiving Messages
1. Verify the SNS subscription is **Confirmed** in AWS
2. Check that Amazon Seller Central has the correct SNS Topic ARN
3. Monitor your flask logs: `tail -f logs/app.log | grep SNS`
4. Try manually triggering a test message in Seller Central

### Signature Verification Failed
- Check that cryptography library is installed: `pip list | grep cryptography`
- Verify you're using HTTPS (SNS requires it)
- Check app logs for specific error

## Under the Hood

The requirements were already met:
- ✓ `cryptography` - for signature verification (just installed)
- ✓ `python-amazon-sp-api` - already installed
- ✓ Public HTTPS URL - your web host provides this
- ✓ Flask app - you're running this

## Disabling SNS

If you want to go back to polling instead, just disable the sync:
- Edit `/api/mail-center/sync` to skip Amazon: `if store_req in ('all', 'ebay'): ...`

## Important Notes

⚠️ **Your public URL must stay accessible 24/7** for SNS delivery to work  
⚠️ **First message will be delayed** by SNS confirmation (usually seconds)  
⚠️ **HTTPS is required** (SNS won't send to plain HTTP)  
⚠️ **Messages are not guaranteed in order** from SNS  

## Next Steps

1. Create the SNS topic in AWS
2. Set up the subscription
3. Register it in Seller Central
4. Test with the script above
5. Verify messages appear in mail-center when customers message you

Questions? Check the app logs or review this guide!
