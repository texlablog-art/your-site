from flask import Flask, jsonify, render_template, request, redirect, url_for, session, flash
from pymongo import MongoClient
from dotenv import load_dotenv
from bson.objectid import ObjectId
from datetime import datetime
import random
import string
import secrets
import base64
import json
import requests
import os 

load_dotenv()
app = Flask(__name__)
app.secret_key = "secret123"

# MongoDB Connection
client = MongoClient(os.getenv("MONGO_URI"))
db = client['myshop_db']
products_db = db.products
banners_db = db.banners
orders_db = db.orders
promo_db = db['promo_codes']
campaign_db = db['campaigns']
user_collection_db = db['user_collections']

IMGBB_API_KEY = "0bb1747f7045ccee9cc03c792b828a67"

def upload_to_imgbb(file):
    """Uploads a file object to ImgBB and returns the direct link."""
    url = "https://api.imgbb.com/1/upload"
    payload = {"key": IMGBB_API_KEY}
    files = {"image": file.read()}
    response = requests.post(url, payload, files=files)
    if response.status_code == 200:
        return response.json()['data']['url']
    return None

@app.route('/')
def index():
    # Fetching products and banners, sorted by newest first
    products = list(products_db.find().sort("_id", -1))
    banners = list(banners_db.find().sort("_id", -1))
    return render_template('index.html', products=products, banners=banners)

@app.route('/add_to_cart/<id>')
def add_to_cart(id):
    # 1. Initialize cart if it doesn't exist
    if 'cart' not in session or not isinstance(session['cart'], dict):
        session['cart'] = {}
    
    cart = session['cart']
    
    # 2. Add product to cart (or increment quantity)
    cart[id] = cart.get(id, 0) + 1
    
    # 3. Save session
    session['cart'] = cart
    session.modified = True
    
    # --- NEW LOGIC FOR DIRECT CHECKOUT ---
    # Look for ?next=checkout in the URL
    next_page = request.args.get('next')
    
    if next_page == 'checkout':
        # Skip the flash message and go straight to checkout
        return redirect(url_for('checkout'))
    
    # Standard behavior for "Add to Cart" button
    flash("Product added to cart!", "success")
    return redirect(request.referrer or url_for('index'))

# --- 1. The Template Filter (Keep at top of app.py) ---
@app.template_filter('last4')
def last4_filter(s):
    # This is a safety fallback in case an order is missing the 10-digit ID
    return str(s)[-6:].upper() if s else "ORDER"

# --- 2. The Corrected Route ---
@app.route('/my-orders', methods=['GET', 'POST'])
def my_orders():
    orders = []
    phone = None
    
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        if phone:
            try:
                # We search your 'orders_db' collection for the 11-digit phone
                # We sort by 'created_at' -1 to show the latest order first
                orders = list(orders_db.find({"phone": phone}).sort("created_at", -1))
                
                if not orders:
                    flash(f"No orders found for {phone}", "info")
            except Exception as e:
                print(f"Database Error: {e}")
                flash("System error. Please try again later.", "danger")
                
    return render_template('my_orders.html', orders=orders, phone=phone)

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('user') == '1' and request.form.get('pass') == '1':
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))
    return render_template('admin_login.html')

@app.route('/about')
def about():
    # This renders the about.html template
    return render_template('about.html')

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        # Collect form data
        contact_msg = {
            "name": request.form.get('name'),
            "email": request.form.get('email'),
            "subject": request.form.get('subject'),
            "message": request.form.get('message'),
            "status": "New",
            "date": datetime.now()
        }
        
        # Save to a new collection called 'messages'
        db.messages.insert_one(contact_msg)
        
        flash("Message sent! We will contact you soon.", "success")
        return redirect(url_for('contact'))
        
    return render_template('contact.html')


@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    # 1. Fetch Orders and Products (Newest first)
    all_orders = list(orders_db.find().sort("created_at", -1))
    all_products = list(products_db.find())
    
    # 2. Helper to encode order for JavaScript
    def prepare_order_for_js(order):
        # Create a copy so we don't mess up the original list
        o = dict(order)
        # Convert non-serializable objects to strings
        o['_id'] = str(o['_id'])
        if 'created_at' in o:
            o['created_at'] = o['created_at'].strftime('%Y-%m-%d %H:%M')
            
        # Convert to JSON string, then Base64 encode it
        json_data = json.dumps(o)
        return base64.b64encode(json_data.encode()).decode()

    # Add the safe string to each order
    for order in all_orders:
        order['safe_json'] = prepare_order_for_js(order)

    # 3. Calculate Dashboard Stats
    stats = {
        "total_revenue": sum(order.get('total', 0) for order in all_orders if order.get('status') != 'Cancelled'),
        "order_count": len(all_orders),
        "pending_orders": len([o for o in all_orders if o.get('status') == 'Pending']),
        "total_products": len(all_products)
    }
    
    return render_template('admin_dashboard.html', 
                           orders=all_orders, 
                           products=all_products,
                           stats=stats,
                           active_page='dashboard')


# --- ADD & VIEW PRODUCTS ---
@app.route('/admin/add_product', methods=['GET', 'POST'])
def add_product():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        # 1. Handle Main Image (Single)
        main_img = request.files.get('main_image')
        main_url = upload_to_imgbb(main_img) if (main_img and main_img.filename != '') else ""
        
        # 2. Handle Multiple Extra Detail Images
        # Use .getlist to catch all selected files
        extra_files = request.files.getlist('extra_images') 
        extra_image_urls = []
        
        for file in extra_files:
            if file and file.filename != '':
                url = upload_to_imgbb(file)
                if url:
                    extra_image_urls.append(url)

        # 3. Handle Video Link & Other Data
        video_url = request.form.get('video_url', '').strip()
        title = request.form.get('title')
        category = request.form.get('category')
        description = request.form.get('description', '')
        
        # Data Validation & Conversion
        try:
            price = int(request.form.get('price', 0))
            del_price = int(request.form.get('del_price', 0)) if request.form.get('del_price') else 0
        except ValueError:
            price = 0
            del_price = 0

        # 4. Save to Database
        product_data = {
            "title": title,
            "category": category, 
            "description": description,
            "price": price,
            "del_price": del_price,
            "main_image": main_url,
            "extra_images": extra_image_urls, # Stored as a LIST now
            "video_url": video_url,
            "in_stock": True, 
            "created_at": datetime.now()
        }
        
        products_db.insert_one(product_data)
        
        flash(f"Product '{title}' published successfully!", "success")
        return redirect(url_for('add_product'))

    # Fetch all products newest first
    all_products = list(products_db.find().sort("created_at", -1))
    return render_template('add_product.html', products=all_products)

# STOCK TOGGLE ROUTE
# TOGGLE STOCK ROUTE
@app.route('/admin/toggle_stock/<id>', methods=['POST'])
def toggle_stock(id):
    product = products_db.find_one({"_id": ObjectId(id)})
    if product:
        # If in_stock exists and is True, make it False. Otherwise make it True.
        new_status = not product.get('in_stock', True)
        products_db.update_one({"_id": ObjectId(id)}, {"$set": {"in_stock": new_status}})
        flash("Stock status updated!", "info")
    return redirect(url_for('add_product'))

# EDIT PRODUCT ROUTE
@app.route('/admin/edit_product/<product_id>', methods=['POST'])
def edit_product(product_id):
    # Get form data
    title = request.form.get('title')
    price = request.form.get('price')
    del_price = request.form.get('del_price')
    category = request.form.get('category')
    video_url = request.form.get('video_url')
    
    # Update MongoDB (example)
    db.products.update_one(
        {'_id': ObjectId(product_id)},
        {'$set': {
            'title': title,
            'price': price,
            'del_price': del_price,
            'category': category,
            'video_url': video_url
        }}
    )
    return redirect('/admin/add_product')

@app.route('/admin/delete_product/<id>', methods=['POST'])
def delete_product(id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    try:
        # Perform the deletion
        products_db.delete_one({"_id": ObjectId(id)})
        flash("Product deleted successfully", "warning")
    except Exception as e:
        flash(f"Error: {e}", "danger")
        
    return redirect(url_for('add_product')) # Redirect back to the inventory list

# --- 2. ADD BANNER ROUTE ---
@app.route('/admin/banners', methods=['GET', 'POST'])
def add_banner():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    if request.method == 'POST':
        banner_file = request.files.get('banner_image')
        banner_url = upload_to_imgbb(banner_file) if banner_file else ""
        
        if banner_url:
            banners_db.insert_one({
                "image_url": banner_url,
                "created_at": datetime.now()
            })
            flash("Banner uploaded successfully!", "success")
        else:
            flash("Failed to upload banner.", "danger")
        
        return redirect(request.referrer)

    # Fetch existing banners to show on the same page
    all_banners = list(banners_db.find().sort("created_at", -1))
    return render_template('banners.html', banners=all_banners)

@app.route('/admin/delete_banner/<id>', methods=['POST'])
def delete_banner(id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    from bson import ObjectId
    banners_db.delete_one({"_id": ObjectId(id)})
    
    flash("Banner deleted successfully", "warning")
    return redirect(url_for('add_banner')) # Or wherever your banner route is

@app.route('/admin/promo')
def admin_promo():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
        
    # Fetch data for the page
    coupons = list(promo_db.find().sort("created_at", -1))
    products = list(products_db.find({}, {"title": 1})) # Get only titles for the dropdown
    
    return render_template('admin_promo.html', 
                           coupons=coupons, 
                           products=products, 
                           active_page='promo') # This triggers the 'active' class

# 1. Admin: Save Promo
@app.route('/admin/add_promo', methods=['POST'])
def add_promo():
    code = request.form.get('code').upper().strip()
    discount = int(request.form.get('discount', 0))
    # Get multiple selected product IDs from dropdown
    applicable_prods = request.form.getlist('products') 

    promo_db.insert_one({
        "code": code,
        "discount_percent": discount,
        "applicable_products": applicable_prods,
        "created_at": datetime.now()
    })
    flash("Promo code created!", "success")
    return redirect('/admin/promo')

@app.route('/admin/delete_promo/<promo_id>', methods=['POST'])
def delete_promo(promo_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    from bson import ObjectId
    promo_db.delete_one({"_id": ObjectId(promo_id)})
    flash("Promo code removed!", "info")
    return redirect('/admin/promo')

# 2. Checkout: Apply Promo (AJAX or Form)
@app.route('/apply_promo', methods=['POST'])
def apply_promo():
    code_input = request.form.get('promo_code').upper().strip()
    cart = session.get('cart', []) # Assuming cart is list of product IDs
    
    promo = promo_db.find_one({"code": code_input})
    
    if not promo:
        return jsonify({"success": False, "message": "Invalid Code"})

    # Check if the promo applies to items in the cart
    # If 'all' is in list, it applies. Otherwise check if cart items match promo list.
    can_apply = False
    if "all" in promo['applicable_products']:
        can_apply = True
    else:
        # Check if at least one item in cart is allowed for this promo
        for item_id in cart:
            if item_id in promo['applicable_products']:
                can_apply = True
                break

    if can_apply:
        session['applied_promo'] = {
            "code": promo['code'],
            "discount": promo['discount_percent']
        }
        return jsonify({"success": True, "discount": promo['discount_percent']})
    
    return jsonify({"success": False, "message": "Code not applicable to items in cart"})

# --- 3. ORDER ACTION (Confirm/Cancel) ---
@app.route('/admin/order_action/<id>/<status>')
def order_action(id, status):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    # Status is passed from HTML as 'Confirmed' or 'Cancelled'
    orders_db.update_one(
        {"_id": ObjectId(id)}, 
        {"$set": {"status": status}}
    )
    flash(f"Order marked as {status}", "info")
    return redirect(url_for('admin_dashboard'))

# --- 4. DELETE ORDER ---
@app.route('/admin/delete_order/<id>')
def delete_order(id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    orders_db.delete_one({"_id": ObjectId(id)})
    flash("Order record deleted.", "danger")
    return redirect(url_for('admin_dashboard'))

# --- 6. LOGOUT ---
@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/product/<id>')
def product_details(id):
    try:
        # Convert the text ID from the URL into a MongoDB ObjectId
        product = products_db.find_one({"_id": ObjectId(id)})
        
        if product:
            # If product exists, show the details page
            return render_template('product_details.html', p=product)
        else:
            return "Product not found", 404
    except Exception as e:
        print(f"Error: {e}")
        return "Invalid Product ID", 400

@app.route('/category/<cat_name>')
def category_page(cat_name):
    # This query finds the category regardless of CAPITALIZATION
    # Example: 'Fashion', 'fashion', and 'FASHION' all work.
    query = {"category": {"$regex": f"^{cat_name}$", "$options": "i"}}
    
    products = list(products_db.find(query).sort("_id", -1))
    
    # Capitalize for the page heading (e.g., 'gadgets' -> 'Gadgets')
    display_title = cat_name.replace('-', ' ').capitalize()
    
    return render_template('category.html', products=products, title=display_title)

@app.context_processor
def inject_theme():
    try:
        settings = db.settings.find_one({"type": "site_config"})
        theme = settings.get('theme', 'default') if settings else 'default'
    except Exception:
        theme = 'default'
    return dict(current_theme=theme)

@app.route('/admin/themes')
def admin_themes():
    settings = db.settings.find_one({"type": "site_config"})
    theme = settings.get('theme', 'default') if settings else 'default'
    # active_page='themes' যোগ করা হয়েছে যাতে সাইডবারে লিঙ্কটি নীল হয়ে থাকে
    return render_template('admin_themes.html', current_theme=theme, active_page='themes')

@app.route('/admin/update-theme', methods=['POST'])
def update_theme():
    new_theme = request.form.get('theme')
    
    # থিমের নাম কি খালি? তবে ডিফল্ট সেট করুন
    if not new_theme:
        new_theme = 'default'
        
    try:
        # ডাটাবেজে আপডেট বা ইনসার্ট করা
        db.settings.update_one(
            {"type": "site_config"},
            {"$set": {"theme": new_theme}},
            upsert=True
        )
        flash(f"Store vibe successfully changed to {new_theme.capitalize()}!", "success")
    except Exception as e:
        flash("Error updating theme: " + str(e), "danger")
        
    return redirect(url_for('admin_themes'))

@app.errorhandler(404)
def page_not_found(e):
    # This catches "Page Not Found" errors
    return render_template('error.html', error_code="404", message="Oops! This page has vanished into thin air."), 404

@app.errorhandler(500)
def server_error(e):
    # This catches "Server Crashed" errors
    return render_template('error.html', error_code="500", message="Something went wrong on our end. We're fixing it!"), 500

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy.html')

def generate_order_id():
    """Generates a unique 10-digit alphanumeric ID (A-Z, 0-9)."""
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choice(chars) for _ in range(10))

@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    cart_dict = session.get('cart', {})
    if not cart_dict:
        return redirect(url_for('index'))

    items_for_summary = []
    grand_total = 0
    
    for pid, qty in cart_dict.items():
        product = products_db.find_one({"_id": ObjectId(pid)})
        if product:
            subtotal = int(product['price']) * qty
            items_for_summary.append({
                "product_id": str(pid),
                "title": product['title'],
                "price": product['price'],
                "quantity": qty,
                "subtotal": subtotal,
                "main_image": product.get('main_image')
            })
            grand_total += subtotal

    if request.method == 'POST':
        alphabet = string.ascii_uppercase + string.digits
        ten_digit_id = ''.join(secrets.choice(alphabet) for i in range(10))
        
        # --- নতুন আপডেট এখানে ---
        # হিডেন ইনপুট থেকে ফাইনাল টোটাল এবং ডিসকাউন্ট নেওয়া হচ্ছে
        final_payable = request.form.get('final_total')
        discount_amount = request.form.get('discount_amount', '0')
        promo_used = request.form.get('applied_promo', '')

        # যদি কোনো কারণে ফাইনাল পেয়েবল না পাওয়া যায়, তবে গ্র্যান্ড টোটাল ব্যবহার হবে
        final_total = float(final_payable) if final_payable else grand_total
        
        pay_method = request.form.get('payment_method')
        trx_id = request.form.get('transaction_id', 'N/A')
        
        order_data = {
            "order_id": ten_digit_id,
            "name": request.form.get('name'),
            "phone": request.form.get('phone'),
            "address": request.form.get('address'),
            "items_details": items_for_summary,
            "subtotal": grand_total,      # আসল দাম
            "discount": float(discount_amount), # কত টাকা ছাড় পেয়েছে
            "promo_code": promo_used,     # কি কোড ব্যবহার করেছে
            "total": final_total,         # এই টাকাটা ইউজার পেমেন্ট করেছে (Database এ সেভ হবে)
            "payment_method": "Cash on Delivery" if pay_method == 'cod' else "Online Payment",
            "transaction_id": trx_id,
            "status": "Pending",
            "created_at": datetime.now()
        }
        
        orders_db.insert_one(order_data)
        session.pop('cart', None)
        session.pop('applied_promo', None) # প্রমো কোড সেশন থেকে ক্লিয়ার করা
        
        return render_template('order_success.html', order_id=ten_digit_id)

    return render_template('checkout.html', items=items_for_summary, total=grand_total)

# --- USER TRACK ROUTE ---
@app.route('/track', methods=['GET', 'POST'])
def track_order():
    order = None
    if request.method == 'POST':
        order_id = request.form.get('order_id').strip().upper()
        order = orders_db.find_one({"order_id": order_id})
        if not order:
            flash("Order ID not found.", "danger")
    return render_template('track.html', order=order)

# --- ADMIN TRACK MANAGEMENT (FIXED) ---
@app.route('/admin/track_manage/<id>', methods=['POST'])
def track_manage(id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    new_status = request.form.get('status').strip() # .strip() যোগ করা হয়েছে স্পেস সরানোর জন্য
    
    # বর্তমান অর্ডারটি চেক করা হচ্ছে এটি ইতিমধ্যে এই স্ট্যাটাসে আছে কি না
    current_order = orders_db.find_one({"_id": ObjectId(id)})
    
    # স্ট্যাটাস যদি আগেরটাই থাকে, তবে নতুন করে হিস্ট্রিতে পুশ করার দরকার নেই
    if current_order and current_order.get('status') == new_status:
        flash(f"Order is already marked as {new_status}", "info")
        return redirect(request.referrer or url_for('admin_track_list'))

    # Update the order status and history log
    orders_db.update_one(
        {"_id": ObjectId(id)},
        {
            "$set": {"status": new_status},
            "$push": {
                "history": {
                    "status": new_status,
                    "time": datetime.now().strftime("%I:%M %p, %d %b %Y")
                }
            }
        }
    )
    
    flash(f"Order updated to {new_status}", "success")
    return redirect(request.referrer or url_for('admin_track_list'))

@app.route('/admin/track')
def admin_track_list():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    # Fetch all orders, newest first
    orders = list(orders_db.find().sort("_id", -1))
    return render_template('admin_track.html', orders=orders)

# --- VIEW CART ---
@app.route('/cart')
def view_cart():
    # 1. Get the cart. Force it to be a dictionary to avoid 500 errors
    cart_dict = session.get('cart', {})
    
    # If someone has the OLD list-style cart in their browser, clear it
    if isinstance(cart_dict, list):
        session['cart'] = {}
        cart_dict = {}

    items = []
    total = 0
    
    # 2. Iterate through dictionary (ID is key, Qty is value)
    for pid, qty in cart_dict.items():
        try:
            product = products_db.find_one({"_id": ObjectId(pid)})
            if product:
                # Calculate subtotal for this specific item
                price = int(product.get('price', 0))
                subtotal = price * qty
                
                # Add these values to the product object for the HTML
                product['quantity'] = qty
                product['subtotal'] = subtotal
                
                items.append(product)
                total += subtotal
        except Exception as e:
            print(f"Error processing item {pid}: {e}")
            continue
            
    return render_template('cart.html', items=items, total=total)

@app.route('/remove_from_cart/<id>')
def remove_from_cart(id):
    cart = session.get('cart', {})
    if id in cart:
        cart.pop(id)
        session['cart'] = cart
        session.modified = True
        flash("Item removed from cart.")
    return redirect(url_for('view_cart'))

@app.route('/update_cart/<id>/<action>')
def update_cart(id, action):
    cart = session.get('cart', {})
    if id in cart:
        if action == 'plus':
            cart[id] += 1
        elif action == 'minus' and cart[id] > 1:
            cart[id] -= 1
        session['cart'] = cart
        session.modified = True
    return redirect(url_for('view_cart'))

if __name__ == '__main__':

    app.run(debug=True)
