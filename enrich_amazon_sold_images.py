"""
One-time script to enrich existing Amazon sold orders with images from amazonStore.db
"""
from DBmanager import enrich_amazon_sold_images

if __name__ == '__main__':
    print("=== Enriching Amazon Sold Orders with Images ===\n")
    updated_count = enrich_amazon_sold_images()
    print(f"\n🎉 Complete! Updated {updated_count} Amazon sold orders with images")
