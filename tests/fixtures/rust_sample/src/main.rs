/// 几何工具 —— Rust 解析器回归 fixture。
struct Point {
    x: f64,
    y: f64,
}

impl Point {
    /// 新建一个点。
    fn new(x: f64, y: f64) -> Point {
        Point { x, y }
    }

    /// 计算到另一个点的欧氏距离。
    fn dist(&self, other: &Point) -> f64 {
        let dx = self.x - other.x;
        let dy = self.y - other.y;
        (dx * dx + dy * dy).sqrt()
    }
}

/// 矩形面积。
fn area(w: f64, h: f64) -> f64 {
    w * h
}

fn main() {
    let a = Point::new(0.0, 0.0);
    let b = Point::new(3.0, 4.0);
    let d = a.dist(&b);
    let ar = area(2.0, 3.0);
    println!("{} {}", d, ar);
}
