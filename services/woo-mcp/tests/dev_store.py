"""The fake store with a little seed data, for running the whole stack locally.

    uvicorn tests.dev_store:app --port 18911   (from services/woo-mcp)
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.fake_store import FakeStore

store = FakeStore()
# SKUs match the simulated supplier's catalog, so "what is low and what would it cost to restock" has an answer.
for name, sku, price, stock in [
    ("Training Ball", "SUP-BALL-5", "25.00", 4),
    ("Marker Cones", "SUP-CONE-20", "12.00", 25),
    ("Training Bibs", "SUP-BIB-10", "30.00", 2),
    ("Ball Pump", "SUP-PUMP", "8.00", 0),
    ("Team Jersey", "SUP-JERSEY", "24.00", 40),
]:
    pid = store._new_id()
    store.products[pid] = {"id": pid, "name": name, "sku": sku, "status": "publish", "price": price,
                           "regular_price": price, "stock_status": "instock" if stock else "outofstock", "stock_quantity": stock,
                           "permalink": f"http://shop.urumi.test:18080/product/{name.lower().replace(' ', '-')}/"}
by_name = {p["name"]: pid for pid, p in store.products.items()}
now = datetime.now(ZoneInfo(store.timezone)).replace(tzinfo=None, microsecond=0)
store.add_order(status="processing", created=(now - timedelta(hours=1)).isoformat(), items=[(by_name["Training Ball"], "Training Ball", 2)], country="IN")
store.add_order(status="processing", created=(now - timedelta(hours=3)).isoformat(), items=[(by_name["Marker Cones"], "Marker Cones", 3), (by_name["Team Jersey"], "Team Jersey", 1)], country="US")
store.add_order(status="completed", created=(now - timedelta(days=2)).isoformat(), items=[(by_name["Training Bibs"], "Training Bibs", 1)], country="LK")
app = store.app
