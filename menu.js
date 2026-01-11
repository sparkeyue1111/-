// 这里是你的菜单数据库
const categories = [
    { id: "noodle", name: "🍜 面食类" },
    { id: "rice",   name: "🍚 炒饭/盖饭" },
    { id: "bbq",    name: "🍢 烧烤/炸物" }, // 新增分类
    { id: "drink",  name: "🥤 酒水饮料" }
];

const menuItems = [
    // --- 面食 ---
    { id: 101, category: "noodle", name: "招牌红烧牛肉面", price: 12.99, img: "https://via.placeholder.com/100?text=Beef" },
    { id: 102, category: "noodle", name: "重庆小面",       price: 8.50,  img: "" },
    
    // --- 炒饭 ---
    { id: 201, category: "rice",   name: "扬州炒饭",       price: 9.50,  img: "https://via.placeholder.com/100?text=Rice" },
    { id: 202, category: "rice",   name: "回锅肉盖饭",     price: 11.00, img: "" },

    // --- 烧烤 (你后期加菜就复制这一行，改ID和名字即可) ---
    { id: 301, category: "bbq",    name: "羊肉串 (5串)",   price: 10.00, img: "" },
    { id: 302, category: "bbq",    name: "烤鸡翅 (2个)",   price: 6.00,  img: "" },
    { id: 303, category: "bbq",    name: "烤韭菜",         price: 4.00,  img: "" },

    // --- 酒水 ---
    { id: 401, category: "drink",  name: "可口可乐",       price: 2.00,  img: "" },
    { id: 402, category: "drink",  name: "吴哥啤酒",       price: 3.50,  img: "" }
];
