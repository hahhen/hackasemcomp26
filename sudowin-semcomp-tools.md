# sudowin-semcomp MCP — Full Tool Reference

Server: `sudowin-semcomp`
Total tools: **23**

All tools are called via the MCP namespace, e.g. `tools["sudowin-semcomp"].getCart({ cartId })`.

---

## Discovery / browsing (read-only)

### `tools["sudowin-semcomp"].browseOffers`

Browse what is available in an iFood business unit for the user's location. Use when the user wants to see what exists nearby, navigate the inventory, or discover available categories (e.g.: 'what's near me?', 'show me pet shop options', 'I want Japanese restaurants', 'show me desserts'). If there is a specific descriptive term (item, brand, attributes), prefer `searchOffers`. If the intent is personalised (favourites, reorder, recommendations), prefer `getRecommendations`. Canonical pattern: (1) open call with only merchantType + resultKind to get the full taxonomy tree in facets.itemTaxonomy; (2) drill-down with refinements.itemTaxonomy using canonical IDs from facets — never invent taxonomy IDs. Side effects: none (read-only).

```ts
browseOffers({
  /** When true, includes refinement buckets in facets (price, distance, taxonomy, etc.). Defaults to false. @default false */
  includeFacets?: boolean,
  /** iFood business unit to browse. Required — without it the tool cannot scope the inventory. If the user has not stated a vertical, ask before calling. */
  merchantType: "RESTAURANT" | "GROCERIES" | "LIQUORSTORE" | "DRUGSTORE" | "PETSTORE" | "SHOPPING",
  /** Opaque token from the previous response to fetch the next page. Do not invent tokens. */
  nextPageToken?: string,
  /** Number of results per page. Default 10, maximum 25. @default 10 */
  pageSize?: number,
  /**
   * Semantic filters and ordering. All fields are optional. For taxonomy drill-down, pass refinements.itemTaxonomy with canonical IDs received from facets.itemTaxonomy in a previous open call. Conversational mapping: 'cheap' → maxItemPrice + sort:PRICE; 'nearby' → maxDistanceKm + sort:DISTANCE; 'free delivery' → maxDeliveryFee:0 + sort:DELIVERY_FEE; 'fast' → sort:DELIVERY_TIME; 'well-rated' → minRating + sort:RATING; 'open now' → onlyOpen:true; 'with promotion / on sale' → onlyWithPromotion:true.
   */
  refinements?: {
    /** Dietary/nutritional item restrictions (AND semantics). For resultKind=ITEM only. Conversational mapping: 'vegan' → VEGAN; 'lactose-free' → LAC_FREE; 'gluten-free' → GLUTEN_FREE. */
    itemRestrictions?: Array<"LAC_FREE" | "SUGAR_FREE" | "VEGAN" | "VEGETARIAN" | "ORGANIC" | "GLUTEN_FREE">,
    /** Optional drill-down. String array of taxonomy ids taken from facets.itemTaxonomy; do not invent values. Each string is either a single level-2 id (no slash) or parent/child as "level2/level3" with exactly one slash. Several slash-free ids mean results may match any of those level-2 categories. Strings with more than one slash are skipped. */
    itemTaxonomy?: Array<string>,
    /** Maximum delivery fee (BRL). */
    maxDeliveryFee?: number,
    /** Maximum delivery time, in minutes. @integer */
    maxDeliveryTimeMinutes?: number,
    /** Maximum geodesic distance to the merchant (km). */
    maxDistanceKm?: number,
    /** Maximum item price (BRL). For resultKind=MERCHANT, returns merchants with at least one item within this limit. */
    maxItemPrice?: number,
    /** Ceiling for the merchant's minimum order amount (BRL), zero or greater. Keeps merchants whose minimum is at or below this value; a merchant that reports no minimum always passes. Independent of maxItemPrice and maxDeliveryFee — each caps its own value, none caps their sum. @minimum 0 */
    maxMinimumOrderAmount?: number,
    /** UUID v4. Restricts results to this merchant. The merchant must belong to the merchantType provided; otherwise the tool fails with an actionable error. For resultKind=ITEM, this is the canonical path for browsing a single merchant's catalog. */
    merchantId?: string,
    /** Minimum rating score (0–5). */
    minRating?: number,
    /** If true, returns only merchants that are currently open. */
    onlyOpen?: boolean,
    /** If true, returns only promotional content. With resultKind=MERCHANT, merchants with an active voucher; with resultKind=ITEM, items with a promotional price. */
    onlyWithPromotion?: boolean,
    /** Ordering: RELEVANCE (default), DELIVERY_FEE, DELIVERY_TIME, DISTANCE, PRICE. */
    sort?: "RELEVANCE" | "DELIVERY_FEE" | "DELIVERY_TIME" | "DISTANCE" | "PRICE",
  },
  /** Desired result type: MERCHANT (stores) or ITEM (items/products). Defaults to MERCHANT when omitted. */
  resultKind?: "MERCHANT" | "ITEM",
  /** User coordinates: { "latitude": float, "longitude": float }. */
  userLocation: {
    /** Latitude in [-90, 90]. */
    latitude?: number,
    /** Longitude in [-180, 180]. */
    longitude?: number,
  },
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].searchOffers`

Text search for merchants or items on iFood. Use when the user describes what they are looking for in natural language (e.g.: 'vegetarian pizza', 'Madero burger'). Decompose the utterance: the descriptive part goes in `query`; qualifiers like price, distance, fee, or rating go in `refinements`. Do not use this tool if there is no descriptive text — prefer `browseOffers` (browsing) or `getRecommendations` (personalization). Side effects: none (read-only).

```ts
searchOffers({
  /** Optional A/B experiment identifier that selects which search pipeline should be used for this request. */
  experiment?: string,
  /** When true, includes refinement buckets in facets (price, distance, taxonomy, etc.). Defaults to false. @default false */
  includeFacets?: boolean,
  /** Optional intention to guide search results. When omitted, derived from resultKind (MERCHANT → 'merchant', ITEM → 'product'). */
  intention?: "merchant" | "product" | "merchant_exact" | "product_exact",
  /** Restricts the search to an iFood business unit. When absent, the search crosses all verticals. */
  merchantType?: "RESTAURANT" | "GROCERIES" | "LIQUORSTORE" | "DRUGSTORE" | "PETSTORE" | "SHOPPING",
  /** Opaque token from the previous response to fetch the next page. Do not invent tokens. */
  nextPageToken?: string,
  /** Number of results per page. Default 10, maximum 25. @default 10 */
  pageSize?: number,
  /** Free-text user intent — only the descriptive/content part. Qualifiers (price, distance, etc.) go in `refinements`. */
  query: string,
  /**
   * Semantic filters and ordering. All fields are optional. Conversational mapping: 'cheap' → maxItemPrice + sort:PRICE; 'nearby' → maxDistanceKm + sort:DISTANCE; 'free delivery' → maxDeliveryFee:0 + sort:DELIVERY_FEE; 'fast / within X minutes' → maxDeliveryTimeMinutes + sort:DELIVERY_TIME; 'well-rated' → minRating + sort:RATING; 'open now' → onlyOpen:true; 'with promotion / on sale' → onlyWithPromotion:true.
   */
  refinements?: {
    /** Dietary/nutritional item restrictions. For resultKind=ITEM only. Conversational mapping: 'vegan' → VEGAN; 'lactose-free' → LAC_FREE; 'gluten-free' → GLUTEN_FREE. */
    itemRestrictions?: Array<"LAC_FREE" | "SUGAR_FREE" | "VEGAN" | "VEGETARIAN" | "ORGANIC" | "GLUTEN_FREE">,
    /** Optional. String array of taxonomy ids from facets in a prior response; do not invent values. Each string is either a single level-2 id (no slash) or parent/child as "level2/level3" with exactly one slash. Several slash-free ids mean results may match any of those level-2 categories. Strings with more than one slash are skipped. */
    itemTaxonomy?: Array<string>,
    /** Maximum delivery fee (BRL). */
    maxDeliveryFee?: number,
    /** Maximum delivery time ceiling (minutes), applied to deliveryTimeMinutes.max of the quoted delivery method. @integer */
    maxDeliveryTimeMinutes?: number,
    /** Maximum geodesic distance to the merchant (km). */
    maxDistanceKm?: number,
    /** Maximum item price (BRL). For resultKind=MERCHANT, returns merchants with at least one item within this limit. */
    maxItemPrice?: number,
    /** Ceiling for the merchant's minimum order amount (BRL), zero or greater. Keeps merchants whose minimum is at or below this value; a merchant that reports no minimum always passes. Independent of maxItemPrice and maxDeliveryFee — each caps its own value, none caps their sum. @minimum 0 */
    maxMinimumOrderAmount?: number,
    /** UUID v4. Restricts results to this merchant. For resultKind=ITEM, canonical path to search or browse within a store after the user picks a merchant (combine with merchantType when needed). */
    merchantId?: string,
    /** Minimum rating score (0–5). */
    minRating?: number,
    /** If true, returns only merchants that are currently open. */
    onlyOpen?: boolean,
    /** If true, returns only promotional content. With resultKind=MERCHANT, merchants with an active voucher; with resultKind=ITEM, items with a promotional price. */
    onlyWithPromotion?: boolean,
    /** Ordering: RELEVANCE (default), DELIVERY_FEE, DELIVERY_TIME, DISTANCE, PRICE. */
    sort?: "RELEVANCE" | "DELIVERY_FEE" | "DELIVERY_TIME" | "DISTANCE" | "PRICE",
  },
  /** Desired result type: MERCHANT (stores) or ITEM (items/products). Defaults to MERCHANT when omitted. */
  resultKind?: "MERCHANT" | "ITEM",
  /** User coordinates: { "latitude": float, "longitude": float }. */
  userLocation: {
    /** Latitude in [-90, 90]. */
    latitude?: number,
    /** Longitude in [-180, 180]. */
    longitude?: number,
  },
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].getRecommendations`

Return pre-curated lists based on user history, recommendations, or editorial selection with geo context. Use when the user wants to reorder, see favorites, check deals, browse recommendations, or discover featured local spots (e.g.: 'reorder my usual', 'what's on sale', 'my favorites', 'what do you recommend'). Do not use when the user has a specific search term — prefer `searchOffers`. Do not use for generic browsing — prefer `browseOffers`. Side effects: none (read-only).

```ts
getRecommendations({
  /** Curated list source: RECOMMENDED (personalized), RECENT_ORDERS (reorder), FAVORITE_MERCHANTS (favorited stores), BEST_DEALS (active promotions), FEATURED_LOCAL (editorial local highlights). */
  contentMode: "RECOMMENDED" | "RECENT_ORDERS" | "FAVORITE_MERCHANTS" | "BEST_DEALS" | "FEATURED_LOCAL",
  /** Optional vertical filter. When absent, results may cross verticals. */
  merchantType?: "RESTAURANT" | "GROCERIES" | "LIQUORSTORE" | "DRUGSTORE" | "PETSTORE" | "SHOPPING",
  /** Opaque token from the previous response to fetch the next page. Do not invent tokens. */
  nextPageToken?: string,
  /** Number of results per page. Default 10, maximum 25. @default 10 */
  pageSize?: number,
  /**
   * Semantic filters and ordering. All fields are optional. Conversational mapping: 'cheap' → maxItemPrice + sort:PRICE; 'nearby' → maxDistanceKm + sort:DISTANCE; 'fast' → maxDeliveryTimeMinutes + sort:DELIVERY_TIME; 'well-rated' → minRating + sort:RATING; 'open now' → onlyOpen:true; 'with promotion / on sale' → onlyWithPromotion:true.
   */
  refinements?: {
    /** Dietary/nutritional item restrictions (AND semantics). For resultKind=ITEM only. Conversational mapping: 'vegan' → VEGAN; 'lactose-free' → LAC_FREE; 'gluten-free' → GLUTEN_FREE. */
    itemRestrictions?: Array<"LAC_FREE" | "SUGAR_FREE" | "VEGAN" | "VEGETARIAN" | "ORGANIC" | "GLUTEN_FREE">,
    /** Optional. String array of taxonomy ids from facets in a prior response; do not invent values. Each string is either a single level-2 id (no slash) or parent/child as "level2/level3" with exactly one slash. */
    itemTaxonomy?: Array<string>,
    /** Maximum delivery fee (BRL). */
    maxDeliveryFee?: number,
    /** Maximum delivery time, in minutes. @integer */
    maxDeliveryTimeMinutes?: number,
    /** Maximum geodesic distance to the merchant (km). */
    maxDistanceKm?: number,
    /** Maximum item price (BRL). */
    maxItemPrice?: number,
    /** Ceiling for the merchant's minimum order amount (BRL), zero or greater. Keeps merchants whose minimum is at or below this value; a merchant that reports no minimum always passes. Independent of maxItemPrice and maxDeliveryFee — each caps its own value, none caps their sum. @minimum 0 */
    maxMinimumOrderAmount?: number,
    /** Minimum rating score (0–5). */
    minRating?: number,
    /** If true, returns only merchants that are currently open. */
    onlyOpen?: boolean,
    /** If true, returns only promotional content. With resultKind=MERCHANT, merchants with an active voucher; with resultKind=ITEM, items with a promotional price. */
    onlyWithPromotion?: boolean,
    /** Ordering: RELEVANCE (default), DELIVERY_FEE, DELIVERY_TIME, DISTANCE, PRICE. */
    sort?: "RELEVANCE" | "DELIVERY_FEE" | "DELIVERY_TIME" | "DISTANCE" | "PRICE",
  },
  /** Desired result type: MERCHANT (stores) or ITEM (items/products). Defaults to MERCHANT when omitted. Must be compatible with contentMode. */
  resultKind?: "MERCHANT" | "ITEM",
  /** User coordinates: { "latitude": float, "longitude": float }. */
  userLocation: {
    /** Latitude in [-90, 90]. */
    latitude?: number,
    /** Longitude in [-180, 180]. */
    longitude?: number,
  },
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].getMerchant`

Returns the full operational detail of a specific merchant: operating hours, all available delivery methods, supported payment methods, minimum order amount, rating, catalogue summary, and optionally featured items. Use after the user has chosen or confirmed a merchant from a listing result. Do not use this tool to list or search merchants — use `browseOffers` or `searchOffers` for that. Side effects: none (read-only).

```ts
getMerchant({
  /** UUID v4 of the merchant to retrieve. Must come from a merchantId returned by a previous listing call (browseOffers, searchOffers, or getRecommendations). Do not invent or guess merchant IDs. */
  merchantId: string,
  /** User coordinates: { "latitude": float, "longitude": float }. */
  userLocation: {
    /** Latitude in [-90, 90]. */
    latitude?: number,
    /** Longitude in [-180, 180]. */
    longitude?: number,
  },
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].getItem`

Returns the full detail of a specific item: variants and complements (`customizations`), curated attributes (dietary, allergens, descriptors), promotions at item level, and a summary of the merchant that sells it. Use after the user has chosen or confirmed a specific item from a listing result. Do not use this tool to list or search items — use `browseOffers` or `searchOffers` for that. Items currently unavailable are not returned; an invalid or unavailable `itemId` yields an actionable error. Side effects: none (read-only).

```ts
getItem({
  /** UUID v4 of the item to retrieve. Must come from an itemId returned by a previous listing call with resultKind=ITEM, or from the `featuredItems` of `getMerchant`. Do not invent or guess item IDs. */
  itemId: string,
  /** UUID v4 of the merchant that owns the item. Must come from a merchantId returned by a previous listing call (browseOffers, searchOffers, or getRecommendations) or from `getMerchant`. Do not invent or guess merchant IDs. */
  merchantId: string,
  /** User coordinates: { "latitude": float, "longitude": float }. */
  userLocation: {
    /** Latitude in [-90, 90]. */
    latitude?: number,
    /** Longitude in [-180, 180]. */
    longitude?: number,
  },
}): Promise<unknown>
```

---

## Account & profile

### `tools["sudowin-semcomp"].getUser`

Returns the authenticated user's profile information so the agent can personalize responses and understand the user's segmentation. Use for personalization, greetings, or to validate authentication. Does not handle addresses — use `listAddresses` for saved delivery addresses. Behavioral profile (`profileData`) is large: set `includeProfileData: true` when the user asks to be known or personalized (e.g. "personalize this for me", "what do I usually order", "what kind of eater am I"). Prefer reusing a profile already in context instead of requesting it on every getUser.

```ts
getUser({
  /** True when the user wants personalization or asks about their tastes/habits (e.g. "personalize your answer", "what do I usually like to order"); false for plain lookups like name or email. Default false. @default false */
  includeProfileData?: boolean,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].listAddresses`

Returns all active delivery addresses on the user's account with usage analytics. **Always call this FIRST in any purchase flow** — it determines whether to confirm an existing address, let the user choose among saved addresses, or collect a new one. The chosen address must be the **exact** place where delivery should occur. Use the behavioral fields on each address (isLastUsedAddress, isMostUsedAddress30d, topMerchantTypes, topWeekDayShift, ordersByMerchantTypeWeekdayAndShift) to infer the most likely delivery address when multiple addresses are saved. When only one address is saved, use it directly and inform the user — no confirmation needed. When multiple addresses are saved, confirm briefly with the user (e.g., 'Entregar na Casa?') instead of listing all saved addresses. Only present the full list when behavioral data is unavailable, when signals are ambiguous, or when the user rejects or asks for alternatives. When an address has an alias ("Casa" or "Trabalho"), prefer presenting it by alias rather than the full street address. If no address is returned, guide the user to provide a new delivery address and use `createAddress` to save it. Do not construct or guess an addressId from address text. Only use the addressId values returned by this tool or by `createAddress`. Do **not** use this tool to search for new places or to save new addresses (use `createAddress`).

```ts
listAddresses(): Promise<unknown>
```

### `tools["sudowin-semcomp"].createAddress`

Geocodes and saves a new delivery address on the user's account, returning a permanent **addressId**. **When to use:** after `listAddresses` returned empty or the user chose to add a new address. Accepts a free-form **address** string (e.g. "Avenida Paulista, 100, São Paulo, SP"). Geocoding is handled internally — do not split into separate street/city fields. **Two-step flow (default):** call with `address` only → returns `matchConfidence` (`SINGLE` or `MULTIPLE`), `candidates[]`, and each candidate's `candidateId` for user confirmation → call again with the chosen `candidateId`, `address`, and optional `neighborhood`, `complement`, `alias`, `reference` to persist. **Single-shot shortcut:** only when `matchConfidence` is `SINGLE` and the user already provided `address` + `complement` in the same interaction with a resolvable neighborhood, send both together to create directly (`createdDirectly: true`). When `matchConfidence` is `MULTIPLE`, always present the candidates and get user confirmation before persisting. If geocoding omits neighborhood, the server returns candidates and asks for `neighborhood` before persisting. **Neighborhood:** when geocoding does not return neighborhood (bairro), ask the user and send it via the `neighborhood` parameter on confirmation or single-shot. **Mandatory delivery fields:** some regions reject creation without `complement` and/or `reference`. If the tool returns `MISSING_REQUIRED_ADDRESS_FIELDS`, ask the user for the fields named in the error message and retry with the same `candidateId`. **Optional alias** — only **CASA** or **TRABALHO** (case-insensitive, normalized server-side). Identity comes from the authenticated session — do not pass accountId.

```ts
createAddress({
  /** Free-form delivery address string (e.g. "Avenida Paulista, 100, São Paulo, SP"). Required on geocode and confirmation calls unless only confirming with candidateId from a prior response. */
  address?: string,
  /** Optional address nickname: exactly **CASA** or **TRABALHO** (case-insensitive). Empty means no alias. */
  alias?: string,
  /** Candidate identifier from a previous createAddress geocode response. Required to confirm and persist after the user validates the location. */
  candidateId?: string,
  /** Apartment, block, store, etc. Sent to the address API as complement. Include on confirmation or single-shot creation. Some delivery regions require it — if creation fails with MISSING_REQUIRED_ADDRESS_FIELDS naming complement, ask the user and retry. */
  complement?: string,
  /** Neighborhood (bairro). Required when geocoding did not return one — ask the user and send on confirmation or single-shot creation. When provided, it overrides the geocoded neighborhood. */
  neighborhood?: string,
  /** Delivery landmark (gate color, building name, etc.). Sent to the API as reference. Some delivery regions require it — if creation fails with MISSING_REQUIRED_ADDRESS_FIELDS naming reference, ask the user and retry. */
  reference?: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].listPaymentMethods`

Retrieves all available payment methods (PIX, credit card, debit card, etc.) and saved card tokens for a merchant. Call this when the user needs to choose a payment method. Use the merchantId from the cart details. Use the returned payment method IDs when calling setCartPayment.

```ts
listPaymentMethods({
  /** Merchant unique identifier. @format uuid */
  merchantId: string,
}): Promise<unknown>
```

---

## Cart management

> **Shared type — `CartItemInput`.** The `createCart` and `addItemToCart` tools inline the same item shape. To avoid repetition it is shown once here and referenced below. In the raw schema it is nested inline, with `cartItemId` present only on the `createCart` variant.

```ts
/** A cart item with quantity, notes, and selected options. Used in createCart, addItem, and updateItem. */
CartItemInput {
  /** The cartItemId of an existing item. Required when updating an item with updateCartItem; omit when creating a new cart or adding a new item. @default "##default" @format uuid */
  cartItemId?: string | null,
  /** Any special instructions or note the user gave for this item (e.g. "well done", "extra spicy"). Omit if the user did not provide one. @default "##default" */
  customerNote?: string | null,
  /** The catalog item ID the user wants to order. @default "##default" @format uuid */
  itemId: string,
  /** The options the user selected for this item (size, extras, toppings, etc.). Use the option IDs from the catalog item. Leave empty if the item has no options. @default "##default" */
  options?: Array<CartItemOption>,
  /** How many units of the item. Must be at least 1. @default "##default" @format int32 @integer */
  quantity: number,
}

/** A selected option on a cart item (e.g. size choice, extra topping) */
CartItemOption {
  /** Any note the user gave for this specific option. Omit if none. @default "##default" */
  customerNote?: string | null,
  /** Customizations on this option (e.g. removing an ingredient from an extra). Leave empty if none. @default "##default" */
  customizations?: Array<CartItemCustomization>,
  /** The option ID from the catalog item. @default "##default" @format uuid */
  optionId: string,
  /** How many of this option to apply (almost always 1). @default "##default" @format int32 @integer */
  quantity: number,
}

/** A customization applied to an option (e.g. "remove onions", "add bacon") */
CartItemCustomization {
  /** Any note for this specific customization. Omit if none. @default "##default" */
  customerNote?: string | null,
  /** The customization ID from the catalog item's option. @default "##default" @format uuid */
  customizationId: string,
  /** How many of this customization to apply (almost always 1). @default "##default" @format int32 @integer */
  quantity: number,
}
```

### `tools["sudowin-semcomp"].createCart`

Creates a new cart for the user. Use orderId to rebuild a cart from a previous order — all other fields are then derived automatically. Otherwise, provide merchantId, addressId, and items to build a cart from scratch. You may also set delivery and payment methods upfront if the user has already chosen them.

```ts
createCart({
  /** The ID of a previous order to rebuild into a new cart. When provided, merchantId, addressId, and items are all derived from this order — do not send them separately. @format uuid */
  orderId?: string,
  /** The delivery address ID for this cart. Required when creating a cart from scratch (no orderId). Collect this from the user if not already known. @format uuid */
  addressId?: string,
  /** The merchant ID to order from. Required when creating a cart from scratch. Omit when providing an orderId — the merchant is derived from the order. @format uuid */
  merchantId?: string,
  /** The items to place in the new cart. Required when creating a cart from scratch. Omit when providing an orderId — items are derived from the order. */
  items?: Array<CartItemInput>,
  /** The delivery method and optional time slot to set on the cart. Omit if the user has not chosen one yet. */
  deliveryMethod?: {
    /** The delivery method ID from the getCartDeliveryMethods response (e.g. "DELIVERY", "PICKUP"). @default "##default" */
    deliveryMethodId: string,
    /** The time slot ID the user picked from the delivery method's available slots. Omit for ASAP delivery. @default "##default" @format uuid */
    selectedTimeSlotId?: string | null,
  },
  /** The payment method to set on the cart. Omit if the user has not chosen one yet. */
  paymentMethod?: {
    /** The card token ID to charge, if the user chose a saved credit or debit card. Omit for PIX or cash. @default "##default" @format uuid */
    cardTokenId?: string | null,
    /** The payment method ID from the listPaymentMethods response (e.g. "PIX", "CREDIT_CARD"). @default "##default" @format uuid */
    paymentMethodId: string,
  },
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].getCart`

Retrieves the full state of a cart: items, quantities, totals, delivery method, payment method, and applied vouchers. Call this before modifying the cart to see the current state, or to show the user what is in their cart before checkout.

```ts
getCart({
  /** The cart ID from a previous createCart response. @format uuid */
  cartId: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].addItemToCart`

Adds a catalog item to the user's cart. Call this when the user asks to add something to their cart. Provide the cartId, the catalog itemId, and the quantity. Include any options the user selected (size, extras, etc.) and any special notes (e.g. "no onions").

```ts
addItemToCart({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
  /** The catalog item ID the user wants to add. @format uuid */
  itemId: string,
  /** How many units of the item to add. Must be at least 1. @format int32 @integer */
  quantity: number,
  /** Any special instructions or note the user gave for this specific item (e.g. "no onions"). Omit if the user did not provide one. */
  customerNote?: string,
  /** The options the user selected for this item (size, extras, etc.). Use the option IDs from the catalog item. Leave empty if the item has no options or the user did not choose any. */
  options?: Array<CartItemOption>,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].updateCartItem`

Updates the quantity, options, or notes of an item already in the cart. Send the full new state of the item (quantity, options, and notes) — not just the changed fields. The cartItemId must match an item from the getCart response.

```ts
updateCartItem({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
  /** The cartItemId of the item to update, as returned in the cart's items list from getCart. @format uuid */
  cartItemId: string,
  /** The catalog item ID the user wants. Must match the item already in the cart. @format uuid */
  itemId: string,
  /** The new quantity for this item. @format int32 @integer */
  quantity: number,
  /** The updated note or special instructions for this item. Omit to keep the existing note. */
  customerNote?: string,
  /** The full list of options the user selected, including any changes. Send the complete set — not just the added or removed options. */
  options?: Array<CartItemOption>,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].removeItemFromCart`

Removes a specific item from the cart. Call this when the user asks to remove or delete an item. The cartItemId must match an item from the getCart response.

```ts
removeItemFromCart({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
  /** The cartItemId of the item to remove, as returned in the cart's items list from getCart. @format uuid */
  cartItemId: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].getCartDeliveryMethods`

Lists the available delivery options (delivery, pickup, etc.) and their time slots for a cart. Call this when the user needs to choose how they want to receive their order. The cart must already have a delivery address set. Use the returned delivery method IDs when calling setCartDelivery.

```ts
getCartDeliveryMethods({
  /** The cart ID from a previous createCart response. The cart must already have a delivery address set. @format uuid */
  cartId: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].setCartDelivery`

Selects a delivery method and optionally a time slot for the cart. Call this after the user has chosen from the options returned by getCartDeliveryMethods. Use the exact delivery method ID from that response. Include a selectedTimeSlotId for scheduled delivery, or omit it for ASAP delivery.

```ts
setCartDelivery({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
  /** The delivery method ID the user chose (e.g. "DELIVERY", "TAKEOUT"). Use the exact ID from the getCartDeliveryMethods response. */
  deliveryMethodId: string,
  /** The time slot ID the user selected, if the delivery method supports scheduled delivery. @format uuid */
  selectedTimeSlotId?: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].setCartPayment`

Selects a payment method for the cart. Call this after the user has chosen a payment option from the listPaymentMethods response. Use the exact payment method ID (e.g. "PIX", "CREDIT_CARD") from that response. Include cardTokenId only when paying with a saved credit or debit card — omit it for PIX or other non-card methods.

```ts
setCartPayment({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
  /** Required when paymentMethod refers to a card payment method (e.g. CREDIT_CARD, DEBIT_CARD). Must be the exact token ID returned in the 'cardTokenId' field of the listPaymentMethods response for the chosen card. Do NOT invent or reuse values — always copy from listPaymentMethods. Omit (leave null) for non-card methods such as PIX. @format uuid */
  cardTokenId?: string,
  /** The payment method ID the user chose (e.g. "PIX", "CREDIT_CARD"). Use the exact ID from the listPaymentMethods response. @format uuid */
  paymentMethodId: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].listVouchers`

Lists the vouchers available for the cart. Call this when the user wants to see which coupons or promo codes they can apply.

```ts
listVouchers({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].setVoucher`

Applies a voucher or discount code to the cart. Call this when the user wants to apply a coupon or promo code they selected. Use a code and campaignId returned by the listVouchers tool.

```ts
setVoucher({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
  /** The voucher code to apply, as returned by the listVouchers tool */
  code: string,
  /** The campaign ID of the voucher, as returned by the listVouchers tool. @format uuid */
  campaignId: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].removeVoucher`

Removes the voucher or discount code currently applied to the cart. Call this when the user wants to remove a coupon or promo code they previously added.

```ts
removeVoucher({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
}): Promise<unknown>
```

---

## Checkout & orders

### `tools["sudowin-semcomp"].submitOrder`

Places the order for the given cart. Call getCart first to show the cart details to the user, then ask the user to explicitly confirm they want to submit the order. Only call this tool after the user has confirmed. Set userConsent to true only when the user has explicitly agreed. If the user provides an identification document, pass it in the document field. Accepted documents: CPF (Brazilian individuals), CNPJ (Brazilian businesses), or Passport (foreign accounts).

```ts
submitOrder({
  /** The cart ID from a previous createCart or getCart response. @format uuid */
  cartId: string,
  /** Set to true only after asking the user to confirm the order and they explicitly agree. Defaults to false — the tool will reject the request if left as false. */
  userConsent?: boolean,
  /** The user's identification document. For Brazilian accounts: CPF (individuals) or CNPJ (businesses). For foreign accounts: Passport number. Collect this if there's an error about this field when trying to checkout the cart. Omit if the user has not provided it. */
  document?: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].getOrder`

Get detailed order information — merchant, items, payment, delivery address, and verification code

```ts
getOrder({
  /** The order ID (UUID) to get details for */
  orderId: string,
}): Promise<unknown>
```

### `tools["sudowin-semcomp"].trackOrder`

Track an order by ID — returns current status, event history, and hints for the agent on when to poll again

```ts
trackOrder({
  /** The order ID (UUID) to track */
  orderId: string,
  /** Opaque token from agentHints.moreStatusEvents to fetch older events */
  nextPageToken?: string,
}): Promise<unknown>
```
