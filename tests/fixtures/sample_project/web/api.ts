import { formatPrice } from "./util";

/** 商品 API クライアント */
export class ItemApi {
  constructor(private baseUrl: string) {}

  async fetchItem(id: string): Promise<string> {
    const res = await fetch(`${this.baseUrl}/items/${id}`);
    const data = await res.json();
    return formatPrice(data.price);
  }
}

export const createApi = (url: string) => {
  return new ItemApi(url);
};
