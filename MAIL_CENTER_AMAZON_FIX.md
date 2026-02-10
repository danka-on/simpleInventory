# Mail-Center Amazon Messages - Solution Implemented

## The Original Problem
Amazon's SP-API Messaging endpoint (`get_messaging_actions_for_order()`) is **outbound only** - it can't fetch customer messages. It only returns "what types of messages can you send?" which isn't useful for centralized messaging.

## The Solution: SNS Webhooks ✓

Instead of trying to poll for messages (which isn't possible), the system now uses **Amazon SNS** to receive **instant notifications** when customers send messages.

### What Changed

1. **New webhook endpoint**: `/api/amazon-sns-webhook`
   - Accepts real-time notifications from Amazon
   - Verifies signatures (ensures it's really Amazon)
   - Stores in mail-center automatically

2. **Required library installed**: `cryptography` 
   - Used for SNS signature verification

3. **Both delivery methods now available**:
   - **Polling** (original): `/api/mail-center/sync` checks for actions periodically
   - **Push** (new): SNS sends notifications instantly when they arrive

## How to Set Up SNS

See [SNS_SETUP_GUIDE.md](SNS_SETUP_GUIDE.md) for complete step-by-step instructions.

**Quick overview:**
1. Create SNS topic in AWS
2. Create HTTPS subscription pointing to `/api/amazon-sns-webhook`
3. Register the topic in Amazon Seller Central
4. Done - messages will now appear in real-time

## Benefits

✓ **Real-time delivery** - Messages appear instantly (not after 5+ minutes)  
✓ **No polling needed** - Reduced API calls to Amazon  
✓ **Secure** - SNS signatures verified  
✓ **Lightweight** - Just stores order notifications, no heavy parsing  

## Backward Compatibility

✓ Original polling via `/api/mail-center/sync` still works  
✓ Both methods can run simultaneously  
✓ No breaking changes to existing code  

## Technical Details

- **Endpoint**: POST `/api/amazon-sns-webhook`
- **Security**: RSA signature verification using Amazon's certificates
- **Storage**: Uses existing `storemail.db` and `_mail_store_upsert_external()` function
- **Message format**: Simple "Message received on order [ID]" notifications
- **Link**: Messages link to Seller Central for full conversation

## Files Involved

- `app.py` - Lines 10741-10873: SNS webhook and signature verification
- `SNS_SETUP_GUIDE.md` - Complete setup documentation
- `storemail.db` - Where messages are stored (existing database)

## Next Steps

1. **Read**: [SNS_SETUP_GUIDE.md](SNS_SETUP_GUIDE.md)
2. **Setup**: Follow the 4 steps to create SNS topic and subscription
3. **Test**: Use the test command in the guide to verify
4. **Monitor**: Watch mail-center for incoming messages

## If You Don't Want SNS

If SNS seems like too much setup, you can just leave polling enabled and check Seller Central manually for customer conversations. The original `_mail_sync_amazon()` function will still show available messaging action links.

## Troubleshooting

See [SNS_SETUP_GUIDE.md](SNS_SETUP_GUIDE.md#troubleshooting) for common issues and fixes.

