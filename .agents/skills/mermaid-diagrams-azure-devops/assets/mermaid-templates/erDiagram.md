# ER Diagram Template

::: mermaid
    erDiagram
      CUSTOMER ||--o{ ORDER : places
      ORDER ||--|{ ORDER_ITEM : contains
      PRODUCT ||--o{ ORDER_ITEM : appears_in
      CUSTOMER {
        string customer_id
        string display_name
      }
      ORDER {
        string order_id
        date created_at
      }
      PRODUCT {
        string product_id
        string name
      }
:::
